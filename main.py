"""Example entry point for running a replay processing queue."""

from pathlib import Path
import logging

from config.settings import AppConfig, ScreenRegion
from core.queue_runner import ReplayQueueRunner, ReplayJob, PlaybackMode
from utils.logging_setup import setup_logging

setup_logging(Path(r"D:\projects\Python\replay_cut_bot\logs"))
logger = logging.getLogger(__name__)


def build_config() -> AppConfig:
    cfg = AppConfig()

    # Dota 2 / Steam settings.
    cfg.steam.steam_install_path = r""

    # KDA OCR settings for FULL_WATCH mode.
    cfg.kda_ocr.tesseract_cmd = r"D:\Programs\Tesseract\tesseract.exe"
    cfg.kda_ocr.region = ScreenRegion(x=15, y=90, width=230, height=28)
    cfg.kda_ocr.poll_interval_sec = 1.0
    cfg.kda_ocr.required_consistent_reads = 1

    # OBS settings.
    cfg.obs.host = "localhost"
    cfg.obs.port = 4455
    cfg.obs.password = ""
    cfg.obs.pre_kill_buffer_seconds = 10  # Used in FULL_WATCH mode.

    # Timeline settings for TIMELINE_JUMPS mode.
    cfg.timeline.default_lead_in_seconds = 20
    cfg.timeline.tick_rate = 30
    # Disable relative mode if repeated jumps drift in a single replay.
    cfg.timeline.goto_is_relative = False
    cfg.timeline.pregame_offset_seconds = 60 * 3
    cfg.timeline.pause_max_wait_sec = 90.0

    # Output settings.
    cfg.output.output_root_dir = r"D:\projects\Python\replay_cut_bot\videos"

    return cfg


def main() -> None:
    app_config = build_config()

    jobs = [
        # Job 1: full match watch with automatic kill detection.
        ReplayJob(
            match_id=8881954468,
            mode=PlaybackMode.FULL_WATCH,
            spectator_index=2,
            post_match_buffer_sec=20.0,
        ),
        # Job 2: process the match with replay automation.
        ReplayJob(
            match_id=8893565425,
            mode=PlaybackMode.TIMELINE_JUMPS,
            spectator_index=9,
            timelines=['20:00', '30:00']
        ),
    ]

    def on_job_started(job: ReplayJob) -> None:
        logger.info(">>> Начинаю задачу: матч %s (%s)", job.match_id, job.mode.value)

    def on_job_finished(job: ReplayJob) -> None:
        logger.info(">>> Задача завершена: матч %s", job.match_id)

    def on_error(message: str) -> None:
        logger.error("Ошибка очереди: %s", message)

    runner = ReplayQueueRunner(
        app_config,
        on_job_started=on_job_started,
        on_job_finished=on_job_finished,
        on_error=on_error,
    )

    try:
        runner.run(jobs)
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C), останавливаю очередь...")
        runner.stop()
    finally:
        runner.obs_controller.disconnect()
        logger.info("Скрипт завершил работу.")


if __name__ == "__main__":
    main()