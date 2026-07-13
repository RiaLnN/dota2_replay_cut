"""Background kill watcher based on OCR KDA updates and OBS replay saves."""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

from core.kda_ocr import KDAOcrReader, KDAReading, RegionNotConfiguredError
from core.obs_controller import OBSController

logger = logging.getLogger(__name__)

OnKdaUpdate = Callable[[KDAReading], None]
OnKillRecorded = Callable[[int, Path], None]
OnError = Callable[[str], None]


def _noop(*_args, **_kwargs) -> None:
    return None


class KillWatcher:
    """Track hero KDA and save one clip for each new kill."""

    def __init__(
        self,
        ocr_reader: KDAOcrReader,
        obs_controller: OBSController,
        output_dir: Path,
        name_template: str,
        poll_interval_sec: float,
        save_timeout_sec: float,
        required_consistent_reads: int,
        post_kill_delay_sec: float = 0.0,
        on_kda_update: OnKdaUpdate = _noop,
        on_kill_recorded: OnKillRecorded = _noop,
        on_error: OnError = _noop,
    ):
        self.ocr_reader = ocr_reader
        self.obs_controller = obs_controller
        self.output_dir = Path(output_dir)
        self.name_template = name_template
        self.poll_interval_sec = poll_interval_sec
        self.save_timeout_sec = save_timeout_sec
        self.required_consistent_reads = max(1, required_consistent_reads)
        # Real-time delay after kill detection before saving replay buffer.
        self.post_kill_delay_sec = max(0.0, post_kill_delay_sec)

        self.on_kda_update = on_kda_update
        self.on_kill_recorded = on_kill_recorded
        self.on_error = on_error

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._clips_recorded = 0
        self._confirmed_reading: Optional[KDAReading] = None
        self._pending_reading: Optional[KDAReading] = None
        self._pending_count = 0

    # ------------------------------------------------------------------ #

    @property
    def kills_recorded(self) -> int:
        """Return number of saved clips."""
        return self._clips_recorded

    def start(self) -> None:
        """Start background watcher thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("KillWatcher уже запущен.")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._clips_recorded = 0
        self._confirmed_reading = None
        self._pending_reading = None
        self._pending_count = 0

        self._thread = threading.Thread(target=self._run, name="KillWatcher", daemon=True)
        self._thread.start()
        logger.info("Слежение за киллами запущено (опрос каждые %.1f сек).", self.poll_interval_sec)

    def stop(self) -> None:
        """Stop watcher thread and wait for termination."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        logger.info("Слежение за киллами остановлено. Сохранено клипов: %d", self._clips_recorded)

    # Background loop.

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                reading = self.ocr_reader.read_kda()
            except RegionNotConfiguredError as exc:
                self.on_error(str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - transient OCR error should not stop watcher
                logger.debug("Ошибка при чтении KDA: %s", exc)
                reading = None

            if reading is not None:
                self.on_kda_update(reading)
                self._process_reading(reading)

            self._stop_event.wait(self.poll_interval_sec)

    def _process_reading(self, reading: KDAReading) -> None:
        """Apply debounce and handle confirmed kill count changes."""
        if reading == self._pending_reading:
            self._pending_count += 1
        else:
            self._pending_reading = reading
            self._pending_count = 1

        if self._pending_count < self.required_consistent_reads:
            return  # Not enough confirmations yet.

        confirmed = reading
        if self._confirmed_reading is None:
            # First stable reading defines baseline state.
            self._confirmed_reading = confirmed
            logger.info(
                "Начальное состояние KDA героя: %d/%d/%d",
                confirmed.kills, confirmed.deaths, confirmed.assists,
            )
            return

        if confirmed.kills > self._confirmed_reading.kills:
            self._handle_new_kill(confirmed)

        self._confirmed_reading = confirmed

    def _handle_new_kill(self, reading: KDAReading) -> None:
        previous_kills = self._confirmed_reading.kills if self._confirmed_reading is not None else reading.kills - 1
        kills_gained = reading.kills - previous_kills

        logger.info(
            "Килл №%d! Текущее KDA героя: %d/%d/%d. Запрашиваю сохранение буфера повторов OBS...",
            reading.kills, reading.kills, reading.deaths, reading.assists,
        )
        if kills_gained > 1:
            logger.warning(
                "Счётчик килов вырос сразу на %d за один опрос (было %d, стало %d) — вероятно, "
                "несколько килов произошли почти одновременно, либо один из них попал в окно "
                "задержки после предыдущего килла. Отдельного клипа для промежуточных килов "
                "не будет — в имени файла используется реальный счётчик килов из игры (%d), "
                "а не порядковый номер сохранённого клипа, так что пропуск сразу виден по имени.",
                kills_gained, previous_kills, reading.kills, reading.kills,
            )

        # Use in-game kill counter as clip index to keep filenames aligned with match events.
        index = reading.kills

        if self.post_kill_delay_sec > 0:
            logger.info(
                "Жду ещё %.1f сек. реального времени, чтобы в клип попали кадры ПОСЛЕ килла...",
                self.post_kill_delay_sec,
            )
            # KDA polling pauses during delay and resumes immediately after.
            if self._stop_event.wait(self.post_kill_delay_sec):
                return  # Stop requested during delay.

        try:
            saved_path = self.obs_controller.save_replay_buffer_and_wait(self.save_timeout_sec)
            final_path = self._move_into_place(saved_path, index)
            self._clips_recorded += 1
            logger.info("Килл №%d сохранён: %s", index, final_path)
            self.on_kill_recorded(index, final_path)
        except Exception as exc:  # noqa: BLE001 - report error and continue watching
            logger.error("Не удалось сохранить фрагмент для килла №%d: %s", index, exc)
            self.on_error(f"Килл №{index}: не удалось сохранить фрагмент ({exc})")

    def _move_into_place(self, saved_path: Path, index: int) -> Path:
        """Move OBS-saved clip into output_dir with configured filename."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ext = saved_path.suffix
        filename = self.name_template.format(index=index, ext=ext)
        dest = self.output_dir / filename
        shutil.move(str(saved_path), str(dest))
        return dest