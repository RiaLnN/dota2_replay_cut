"""Timeline jump recorder with OCR-based clock correction."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from config.settings import TimelineConfig
from core.clock_ocr import ClockOcrReader
from core.obs_controller import OBSController
from core.replay_player import ReplayPlayer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimelineTarget:
    """Single timeline target for seeking and recording."""

    label: str  # Example: "28:52"
    tick: int  # Initial rough absolute tick.
    target_game_seconds: int  # Target in-game seconds for correction.
    lead_in_seconds: Optional[int] = None


OnClipRecorded = Callable[[int, TimelineTarget, Path], None]
OnError = Callable[[str], None]
OnOcrAttempt = Callable[[TimelineTarget, int, int], None]
OnOcrSuccess = Callable[[TimelineTarget, int, int], None]
OnOcrFailed = Callable[[TimelineTarget, int], None]


def _noop(*_args, **_kwargs) -> None:
    return None


class TimelineRecorder:
    """Run timeline targets on an active replay and save clips."""

    def __init__(
        self,
        replay_player: ReplayPlayer,
        obs_controller: OBSController,
        clock_ocr: ClockOcrReader,
        timeline_config: TimelineConfig,
        output_dir: Path,
        name_template: str,
        save_timeout_sec: float,
        match_duration_seconds: Optional[int] = None,
        on_clip_recorded: OnClipRecorded = _noop,
        on_error: OnError = _noop,
        on_ocr_attempt: OnOcrAttempt = _noop,
        on_ocr_success: OnOcrSuccess = _noop,
        on_ocr_failed: OnOcrFailed = _noop,
    ):
        self.replay_player = replay_player
        self.obs_controller = obs_controller
        self.clock_ocr = clock_ocr
        self.timeline_config = timeline_config
        self.output_dir = Path(output_dir)
        self.name_template = name_template
        self.save_timeout_sec = save_timeout_sec
        # Match duration is used only to calculate a safe upper tick boundary.
        self.match_duration_seconds = match_duration_seconds
        self.on_clip_recorded = on_clip_recorded
        self.on_error = on_error
        self.on_ocr_attempt = on_ocr_attempt
        self.on_ocr_success = on_ocr_success
        self.on_ocr_failed = on_ocr_failed

        self._stop_event = threading.Event()
        self._clips_recorded = 0

    @property
    def clips_recorded(self) -> int:
        return self._clips_recorded

    def stop(self) -> None:
        self._stop_event.set()

    def run(self, window, targets: List[TimelineTarget], spectator_index: int) -> None:
        self._stop_event.clear()
        self._clips_recorded = 0
        self.output_dir.mkdir(parents=True, exist_ok=True)

        ordered_targets = sorted(targets, key=lambda t: t.tick)
        logger.info("Начинаю обработку %d таймкодов (устойчивая OCR-коррекция)...", len(ordered_targets))

        for idx, target in enumerate(ordered_targets, start=1):
            if self._stop_event.is_set():
                logger.info("Обработка таймкодов остановлена.")
                return
            self._process_target(idx, target, window, spectator_index)

        logger.info("Обработка таймкодов завершена: %d фрагмент(ов).", self._clips_recorded)

    # Safe max tick helpers.

    def _safe_max_tick(self) -> int:
        """Return hard upper bound for any jump target tick."""
        if self.match_duration_seconds is None:
            return 2**31 - 1
        slack = self.timeline_config.max_pregame_and_pause_allowance_seconds
        return int((self.match_duration_seconds + slack) * self.timeline_config.tick_rate)

    def _clamp_tick(self, tick: int) -> int:
        return max(0, min(tick, self._safe_max_tick()))

    # Main target processing.

    def _process_target(self, idx: int, target: TimelineTarget, window, spectator_index: int) -> None:
        lead_in = (
            target.lead_in_seconds
            if target.lead_in_seconds is not None
            else self.timeline_config.default_lead_in_seconds
        )
        desired_seconds = int(target.target_game_seconds - lead_in)

        # 1) Rough jump constrained by safe max tick.
        seek_tick = self._clamp_tick(int(target.tick - lead_in * self.timeline_config.tick_rate))
        logger.info("Таймкод %s (%d/?): 1. Грубый прыжок к тику %d...", target.label, idx, seek_tick)
        try:
            self.replay_player.goto_tick(window, seek_tick, spectator_index=spectator_index)
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось перейти к таймкоду %s: %s", target.label, exc)
            self.on_error(f"Таймкод {target.label}: ошибка перехода ({exc})")
            return

        if self._stop_event.wait(self.timeline_config.post_rough_jump_settle_sec):
            return

        # 2) Iterative OCR correction loop.
        if not self._converge_to_target(target, window, spectator_index, desired_seconds):
            return  # остановлено извне посреди коррекции

        # 3) Real-time wait to accumulate OBS replay buffer.
        wait_sec = lead_in + self.timeline_config.post_jump_wait_extra_sec
        logger.info("Жду %.1f сек. реального времени для буфера OBS...", wait_sec)
        if self._stop_event.wait(wait_sec):
            return

        # 4) Save clip.
        logger.info("Сохраняю фрагмент для таймкода %s...", target.label)
        try:
            saved_path = self.obs_controller.save_replay_buffer_and_wait(self.save_timeout_sec)
            final_path = self._move_into_place(saved_path, idx, target)
            self._clips_recorded += 1
            logger.info("Таймкод %s сохранён: %s", target.label, final_path)
            self.on_clip_recorded(idx, target, final_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось сохранить фрагмент: %s", exc)
            self.on_error(f"Таймкод {target.label}: ошибка сохранения ({exc})")

    def _converge_to_target(self, target: TimelineTarget, window, spectator_index: int, desired_seconds: int) -> bool:
        """Iteratively refine playback position using OCR clock readings."""
        max_attempts = max(0, self.timeline_config.max_correction_attempts)
        tolerance = self.timeline_config.correction_tolerance_seconds
        sanity_bound = self.timeline_config.max_single_correction_seconds

        for attempt in range(1, max_attempts + 1):
            if self._stop_event.is_set():
                return False

            actual_game_seconds = self._read_clock_stable(target)
            if actual_game_seconds is None:
                logger.warning(
                    "OCR не смог прочитать часы для %s (коррекция %d/%d). Оставляю текущую позицию как есть.",
                    target.label, attempt, max_attempts,
                )
                return True

            diff_seconds = desired_seconds - actual_game_seconds
            if abs(diff_seconds) <= tolerance:
                logger.info(
                    "OCR: попали в цель для %s (разница %d сек, попытка %d/%d).",
                    target.label, diff_seconds, attempt, max_attempts,
                )
                return True

            if abs(diff_seconds) > sanity_bound:
                logger.warning(
                    "Таймкод %s: коррекция на %d сек превышает разумный предел (%d сек) — похоже на "
                    "сбой OCR (например, чтение во время паузы), а не на реальное расхождение. "
                    "Коррекция пропущена, использую текущую позицию.",
                    target.label, diff_seconds, sanity_bound,
                )
                return True

            current_tick_now = self.replay_player.estimate_current_tick()
            corrected_tick = self._clamp_tick(current_tick_now + int(diff_seconds * self.timeline_config.tick_rate))

            logger.info(
                "Таймкод %s — OCR (попытка %d/%d): на экране %d сек, нужно %d сек (разница %d сек). "
                "Корректирующий прыжок к тику %d.",
                target.label, attempt, max_attempts, actual_game_seconds, desired_seconds, diff_seconds, corrected_tick,
            )
            try:
                self.replay_player.goto_tick(window, corrected_tick, spectator_index=spectator_index)
            except Exception as exc:  # noqa: BLE001
                logger.error("Не удалось выполнить OCR-коррекцию для %s: %s", target.label, exc)
                self.on_error(f"Таймкод {target.label}: ошибка OCR-коррекции ({exc})")
                return True

            if self._stop_event.wait(self.timeline_config.post_correction_settle_sec):
                return False

        logger.warning(
            "Таймкод %s: не удалось точно попасть за %d попыток коррекции — использую последний результат.",
            target.label, max_attempts,
        )
        return True

    def _read_clock_stable(self, target: TimelineTarget) -> Optional[int]:
        """Read game clock with pause-aware stabilization logic."""
        first = self._read_clock_with_retries(target)
        if first is None or self._stop_event.is_set():
            return first

        if self._stop_event.wait(self.timeline_config.pause_detect_gap_sec):
            return first
        second = self._read_clock_with_retries(target)
        if second is None:
            return first
        if second != first:
            return second  # Clock is advancing; use latest value.

        logger.info(
            "Таймкод %s: часы не изменились за %.1f сек — похоже на паузу в матче. "
            "Жду окончания паузы (не дольше %.0f сек)...",
            target.label, self.timeline_config.pause_detect_gap_sec, self.timeline_config.pause_max_wait_sec,
        )
        deadline = time.monotonic() + self.timeline_config.pause_max_wait_sec
        last_value = second
        while time.monotonic() < deadline:
            if self._stop_event.wait(2.0):
                return last_value
            value = self._read_clock_with_retries(target)
            if value is None:
                continue
            if value != last_value:
                logger.info("Таймкод %s: похоже, пауза закончилась — часы снова идут (%d сек).", target.label, value)
                return value
            last_value = value

        logger.warning(
            "Таймкод %s: часы не сдвинулись за %.0f сек ожидания (очень долгая пауза либо сбой OCR). "
            "Использую последнее прочитанное значение (%d сек) как есть.",
            target.label, self.timeline_config.pause_max_wait_sec, last_value,
        )
        return last_value

    def _read_clock_with_retries(self, target: TimelineTarget) -> Optional[int]:
        attempts = max(1, self.timeline_config.clock_ocr_attempts)
        retry_delay = max(0.0, self.timeline_config.clock_ocr_retry_delay_sec)

        for attempt in range(1, attempts + 1):
            self.on_ocr_attempt(target, attempt, attempts)
            actual_game_seconds = self.clock_ocr.read_game_seconds()
            if actual_game_seconds is not None:
                if attempt > 1:
                    logger.info("OCR прочитал часы с попытки %d/%d: %d сек.", attempt, attempts, actual_game_seconds)
                self.on_ocr_success(target, attempt, actual_game_seconds)
                return actual_game_seconds

            if attempt < attempts:
                logger.debug(
                    "OCR не прочитал часы на попытке %d/%d, повтор через %.2f сек.",
                    attempt, attempts, retry_delay,
                )
                if self._stop_event.wait(retry_delay):
                    return None

        self.on_ocr_failed(target, attempts)
        return None

    def _move_into_place(self, saved_path: Path, index: int, target: TimelineTarget) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ext = saved_path.suffix
        safe_label = target.label.replace(":", "_")
        filename = self.name_template.format(index=index, ext=ext, label=safe_label)
        dest = self.output_dir / filename
        shutil.move(str(saved_path), str(dest))
        return dest