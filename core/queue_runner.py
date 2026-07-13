"""Replay queue runner for sequential multi-match processing."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from config.settings import AppConfig
from core import steam_utils
from core.dota_launcher import DotaLauncher
from core.game_input import GameInputController
from core.kda_ocr import KDAOcrReader
from core.kill_watcher import KillWatcher
from core.obs_controller import OBSController
from core.replay_player import ReplayPlayer
from core.replay_service import MatchInfo, ReplayService
from core.timeline_recorder import TimelineRecorder, TimelineTarget
from core.clock_ocr import ClockOcrReader  # OCR reader for timeline correction.
from utils.time_utils import get_replay_tick, time_str_to_seconds

logger = logging.getLogger(__name__)


class PlaybackMode(str, Enum):
    FULL_WATCH = "full_watch"
    TIMELINE_JUMPS = "timeline_jumps"


@dataclass
class ReplayJob:
    match_id: int
    mode: PlaybackMode
    spectator_index: int
    timelines: List[str] = field(default_factory=list)
    lead_in_seconds: Optional[int] = None
    post_match_buffer_sec: float = 20.0


def _noop(*_args, **_kwargs) -> None:
    return None


class ReplayQueueRunner:
    def __init__(
        self,
        app_config: AppConfig,
        on_job_started: Callable[[ReplayJob], None] = _noop,
        on_job_finished: Callable[[ReplayJob], None] = _noop,
        on_error: Callable[[str], None] = _noop,
    ):
        self.app_config = app_config
        self.replay_service = ReplayService(app_config.replay)
        self.launcher = DotaLauncher(app_config.steam)
        self.game_input = GameInputController(app_config.spectator)
        self.replay_player = ReplayPlayer(
            app_config.spectator, self.launcher, self.game_input, app_config.timeline
        )
        self.obs_controller = OBSController(app_config.obs)
        
        self.clock_ocr = ClockOcrReader(
            tesseract_cmd=app_config.kda_ocr.tesseract_cmd,  # Reuse the same tesseract executable.
            region=app_config.timeline.clock_region
        )

        self.on_job_started = on_job_started
        self.on_job_finished = on_job_finished
        self.on_error = on_error

        self._stop_event = threading.Event()
        self._active_watcher: Optional[KillWatcher] = None
        self._active_recorder: Optional[TimelineRecorder] = None

    def stop(self) -> None:
        self._stop_event.set()
        if self._active_watcher is not None:
            self._active_watcher.stop()
        if self._active_recorder is not None:
            self._active_recorder.stop()

    def run(self, jobs: List[ReplayJob]) -> None:
        if not jobs:
            logger.warning("Очередь реплеев пуста — нечего обрабатывать.")
            return

        self._stop_event.clear()
        replays_dir = self._replays_dir()

        if not self.obs_controller.is_connected:
            logger.info("Подключаюсь к OBS...")
            self.obs_controller.connect()

        window = None
        for job_index, job in enumerate(jobs, start=1):
            if self._stop_event.is_set():
                logger.info("Очередь остановлена.")
                break

            logger.info("=== Задача %d/%d: матч %s, режим «%s» ===", job_index, len(jobs), job.match_id, job.mode.value)
            self.on_job_started(job)
            try:
                window = self._process_job(job, replays_dir, window)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Задача (матч %s) завершилась с ошибкой: %s", job.match_id, exc)
                self.on_error(f"Матч {job.match_id}: {exc}")
            finally:
                self.on_job_finished(job)

        logger.info("Обработка очереди реплеев завершена.")

    def _replays_dir(self) -> Path:
        replays_dir = steam_utils.get_dota_replays_dir(self.app_config.steam)
        if replays_dir is None:
            raise RuntimeError("Не удалось определить папку replays.")
        return replays_dir

    def _process_job(self, job: ReplayJob, replays_dir: Path, window):
        match_info = self.replay_service.get_match_info(job.match_id)
        
        # Download replay.
        demo_path = self.replay_service.download_replay(match_info, replays_dir)
        spectator_index = self._resolve_spectator_index(job)

        # Launch replay.
        if window is None:
            window = self.replay_player.ensure_launched_and_ready()
        self.replay_player.play_demo_only(demo_path, spectator_index, window)

        if job.mode == PlaybackMode.FULL_WATCH:
            self._run_full_watch(job, match_info, window)
        elif job.mode == PlaybackMode.TIMELINE_JUMPS:
            self._run_timeline_jumps(job, window, spectator_index)
        else:
            raise ValueError(f"Неизвестный режим: {job.mode}")

        return window

    def _resolve_spectator_index(self, job: ReplayJob) -> int:
        return job.spectator_index

    def _run_full_watch(self, job: ReplayJob, match_info: MatchInfo, window) -> None:
        buffer_seconds = (
            job.lead_in_seconds if job.lead_in_seconds is not None else self.app_config.obs.pre_kill_buffer_seconds
        )
        if self.app_config.obs.auto_set_buffer_duration:
            self.obs_controller.set_replay_buffer_duration(buffer_seconds)
        self.obs_controller.ensure_replay_buffer_active()

        kda_reader = KDAOcrReader(self.app_config.kda_ocr)
        output_dir = Path(self.app_config.output.output_root_dir) / str(job.match_id)

        watcher = KillWatcher(
            ocr_reader=kda_reader,
            obs_controller=self.obs_controller,
            output_dir=output_dir,
            name_template=self.app_config.output.kill_clip_name_template,
            poll_interval_sec=self.app_config.kda_ocr.poll_interval_sec,
            save_timeout_sec=self.app_config.obs.save_confirm_timeout_sec,
            required_consistent_reads=self.app_config.kda_ocr.required_consistent_reads,
            on_error=self.on_error,
        )
        self._active_watcher = watcher
        watcher.start()

        wait_seconds = match_info.duration + job.post_match_buffer_sec
        self._stop_event.wait(wait_seconds)

        watcher.stop()
        kda_reader.close()
        self._active_watcher = None

    def _run_timeline_jumps(self, job: ReplayJob, window, spectator_index: int) -> None:
        buffer_seconds = (
            job.lead_in_seconds
            if job.lead_in_seconds is not None
            else self.app_config.timeline.default_lead_in_seconds
        )
        if self.app_config.obs.auto_set_buffer_duration:
            self.obs_controller.set_replay_buffer_duration(buffer_seconds)
        self.obs_controller.ensure_replay_buffer_active()

        if not job.timelines:
            logger.warning("У задачи (матч %s) режим timeline_jumps, но список timelines пуст.", job.match_id)
            return

        tick_rate = self.app_config.timeline.tick_rate
        pregame_offset = getattr(self.app_config.timeline, 'pregame_offset_seconds', 90)
        
        # Build timeline targets with expected game time in seconds.
        targets = []
        for label in job.timelines:
            targets.append(
                TimelineTarget(
                    label=label,
                    tick=get_replay_tick(label, tick_rate, pregame_offset),
                    target_game_seconds=time_str_to_seconds(label),
                    lead_in_seconds=job.lead_in_seconds,
                )
            )

        output_dir = Path(self.app_config.output.output_root_dir) / str(job.match_id)
        recorder = TimelineRecorder(
            replay_player=self.replay_player,
            obs_controller=self.obs_controller,
            clock_ocr=self.clock_ocr,  # OCR-based clock reader for refinement.
            timeline_config=self.app_config.timeline,
            output_dir=output_dir,
            name_template=self.app_config.output.timeline_clip_name_template,
            save_timeout_sec=self.app_config.obs.save_confirm_timeout_sec,
            on_error=self.on_error,
            on_ocr_attempt=self._on_ocr_attempt,
            on_ocr_success=self._on_ocr_success,
            on_ocr_failed=self._on_ocr_failed,
        )
        self._active_recorder = recorder
        recorder.run(window, targets, spectator_index)
        self._active_recorder = None

    def _on_ocr_attempt(self, target: TimelineTarget, attempt: int, total_attempts: int) -> None:
        logger.debug("OCR часов: таймкод %s, попытка %d/%d.", target.label, attempt, total_attempts)

    def _on_ocr_success(self, target: TimelineTarget, attempt: int, actual_game_seconds: int) -> None:
        logger.info(
            "OCR часов: таймкод %s успешно прочитан (попытка %d): %d сек.",
            target.label,
            attempt,
            actual_game_seconds,
        )

    def _on_ocr_failed(self, target: TimelineTarget, total_attempts: int) -> None:
        message = (
            f"Таймкод {target.label}: OCR не смог прочитать часы после {total_attempts} попыток, "
            "используется только грубый прыжок."
        )
        logger.warning(message)
        self.on_error(message)