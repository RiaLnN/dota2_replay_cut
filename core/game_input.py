"""Send replay console commands to Dota 2 through keyboard input emulation."""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from config.settings import SpectatorConfig
from core.exceptions import GameWindowNotFoundError

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    try:
        import pydirectinput  # type: ignore
        import pygetwindow  # type: ignore
        import pyperclip  # type: ignore

        pydirectinput.PAUSE = 0.02
    except ImportError:  # pragma: no cover - dependency availability is validated at runtime
        pydirectinput = None  # type: ignore
        pygetwindow = None  # type: ignore
        pyperclip = None  # type: ignore
else:
    pydirectinput = None  # type: ignore
    pygetwindow = None  # type: ignore
    pyperclip = None  # type: ignore


class GameInputController:
    """Find Dota 2 window and send console commands."""

    WINDOW_TITLE_HINT = "Dota 2"

    def __init__(self, config: SpectatorConfig):
        self.config = config

    def is_supported(self) -> bool:
        """Return False when platform or dependencies cannot emulate input."""
        return (
            _IS_WINDOWS
            and pydirectinput is not None
            and pygetwindow is not None
            and pyperclip is not None
        )

    def wait_for_window(self, timeout_sec: float) -> "pygetwindow.Win32Window": # type: ignore
        """Wait for Dota 2 window and raise on timeout."""
        if not self.is_supported():
            raise GameWindowNotFoundError(
                "Автоматическая отправка команд в окно игры поддерживается только "
                "на Windows (нужны пакеты pydirectinput и pygetwindow)."
            )

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            windows = pygetwindow.getWindowsWithTitle(self.WINDOW_TITLE_HINT) # type: ignore
            if windows:
                return windows[0]
            time.sleep(1.0)

        raise GameWindowNotFoundError(
            f"Окно Dota 2 не появилось за {timeout_sec:.0f} сек. "
            "Проверьте, что игра запустилась и не зависла на обновлении."
        )

    def focus_window(self, window) -> None:
        try:
            if window.isMinimized:
                window.restore()
            window.activate()
        except Exception as exc:  # noqa: BLE001 - window activation behavior differs across systems
            logger.warning("Не удалось однозначно активировать окно игры: %s", exc)
        time.sleep(0.3)

    def send_console_command(self, command: str, window: Optional["pygetwindow.Win32Window"] = None) -> None: # type: ignore
        """Open console, paste command, submit, and close console."""
        if not self.is_supported():
            raise GameWindowNotFoundError(
                "Эмуляция ввода недоступна на этой платформе (нужны pydirectinput, "
                f"pygetwindow и pyperclip на Windows) — введите команду вручную в консоли игры: {command}"
            )

        if window is None:
            window = self.wait_for_window(timeout_sec=5.0)
        self.focus_window(window)

        if not pyperclip or not pydirectinput:
            return
        
        delay = self.config.command_send_delay_sec
        logger.debug("Отправляю команду в консоль игры через буфер обмена: %s", command)

        previous_clipboard: Optional[str] = None
        try:
            previous_clipboard = pyperclip.paste()
        except Exception as exc:  # noqa: BLE001 - clipboard may contain unsupported data
            logger.debug("Не удалось прочитать текущее содержимое буфера обмена: %s", exc)

        pyperclip.copy(command)
        try:
            pydirectinput.press(self._console_key_name())
            time.sleep(delay)

            pydirectinput.keyDown("ctrl")
            pydirectinput.press("v")
            pydirectinput.keyUp("ctrl")
            time.sleep(delay)

            pydirectinput.press("enter")
            time.sleep(delay)
            pydirectinput.press(self._console_key_name())
        finally:
            if previous_clipboard is not None:
                try:
                    pyperclip.copy(previous_clipboard)
                except Exception as exc:  # noqa: BLE001 - clipboard restore failure is non-critical
                    logger.debug("Не удалось восстановить буфер обмена: %s", exc)

    def _console_key_name(self) -> str:
        # pydirectinput uses internal key names, and `~` maps to "`".
        key = self.config.console_toggle_key
        return key if key else "`"