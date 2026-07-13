"""Replay launch and spectator focus automation for Dota 2."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from config.settings import SpectatorConfig, TimelineConfig
from core.dota_launcher import DotaLauncher
from core.exceptions import GameWindowNotFoundError
from core.game_input import GameInputController

logger = logging.getLogger(__name__)


class ReplayPlayer:
    """Combine launcher and input controller for replay playback automation."""

    def __init__(
        self,
        config: SpectatorConfig,
        launcher: DotaLauncher,
        game_input: GameInputController,
        timeline_config: TimelineConfig
    ):
        self.config = config
        self.launcher = launcher
        self.game_input = game_input
        self.timeline_config = timeline_config
        self._current_tick = 0

    def launch_and_play(self, demo_path: Path, spectator_index: int, timeline: int | None = None) -> None:
        """Launch Dota 2 if needed and load replay with spectator focus."""
        demo_name = demo_path.stem  # Demo file name without extension for playdemo command.

        if not self.config.auto_send_console_commands:
            self.launcher.launch()
            logger.info(
                "Автоотправка команд отключена в настройках. После загрузки игры введите "
                "в консоли: %s",
                self.config.playdemo_command_template.format(demo_name=demo_name),
            )
            return

        try:
            window = self.ensure_launched_and_ready()
        except GameWindowNotFoundError as exc:
            logger.warning(
                "%s Загрузите реплей вручную командой 'playdemo replays/%s' в консоли игры.",
                exc, demo_name,
            )
            return

        self.play_demo_only(demo_path, spectator_index, window)
        if self.config.timeline_enable:
            if timeline is None:
                raise ValueError("Для перехода по timeline требуется передать значение timeline (тик).")
            self.goto_tick(window, timeline, spectator_index)

        logger.info(
            "Если камера сфокусировалась не на том герое — кликните по его порт­рету "
            "вручную один раз, дальнейшее распознавание KDA на это не влияет."
        )
    
    def ensure_launched_and_ready(self):
        if not self.config.auto_send_console_commands:
            raise RuntimeError(
                "Автоотправка команд в консоль отключена (auto_send_console_commands=False), "
                "режим очереди реплеев требует включённой автоматизации."
            )

        self.launcher.launch()
        window = self.game_input.wait_for_window(self.config.window_appear_timeout_sec)
        logger.info(
            "Окно Dota 2 найдено, жду загрузку главного меню (%.0f сек)...",
            self.config.post_launch_wait_sec,
        )
        time.sleep(self.config.post_launch_wait_sec)
        self._current_tick = 0
        return window

    def play_demo_only(self, demo_path: Path, spectator_index: int, window) -> None:
        if not self.config.auto_send_console_commands:
            raise RuntimeError(
                "Автоотправка команд в консоль отключена (auto_send_console_commands=False), "
                "автозагрузка реплея недоступна."
            )

        demo_name = Path(demo_path).stem
        playdemo_cmd = self.config.playdemo_command_template.format(demo_name=demo_name)
        logger.info("Загружаю реплей: %s", playdemo_cmd)
        self.game_input.send_console_command(playdemo_cmd, window)

        logger.info("Жду загрузку реплея (%.0f сек)...", self.config.post_demo_load_wait_sec)
        time.sleep(self.config.post_demo_load_wait_sec)
        self._current_tick = 0

        self._set_spectator_focus(window, spectator_index)

    def goto_tick(self, window, seek_tick: int, spectator_index: int) -> None:
        tick_to_send = seek_tick
        command_mode = "absolute"
        if self.timeline_config.goto_is_relative:
            tick_to_send = seek_tick - self._current_tick
            command_mode = "relative"

        goto_command = f"demo_goto {tick_to_send}"
        if self.timeline_config.goto_is_relative:
            goto_command = f"{goto_command} relative"

        logger.info(
            "Переход в реплее: %s тик=%d (команда: %s)",
            command_mode,
            seek_tick,
            goto_command,
        )
        self.game_input.send_console_command(goto_command, window)
        time.sleep(self.config.command_send_delay_sec)
        self._current_tick = seek_tick

        if self.timeline_config.resend_follow_hero_after_jump:
            self._set_spectator_focus(window, spectator_index)

    def estimate_current_tick(self, elapsed_seconds: float = 0.0) -> int:
        elapsed_ticks = int(max(0.0, elapsed_seconds) * self.timeline_config.tick_rate)
        return max(0, int(self._current_tick + elapsed_ticks))

    def _set_spectator_focus(self, window, spectator_index: int) -> None:
        logger.info("Переключаю камеру наблюдателя на выбранного героя (индекс %d)...", spectator_index)
        self.game_input.send_console_command(self.config.spectator_mode_command, window)
        time.sleep(self.config.command_send_delay_sec)
        follow_cmd = self.config.follow_hero_command_template.format(index=spectator_index)
        self.game_input.send_console_command(follow_cmd, window)
        time.sleep(self.config.command_send_delay_sec)