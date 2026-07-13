"""Launch Dota 2 through Steam URI handlers."""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import psutil

from config.settings import SteamConfig
from core import steam_utils
from core.exceptions import Dota2NotInstalledError

logger = logging.getLogger(__name__)

# Process name fragments used to detect a running game instance.
_PROCESS_NAME_HINTS = ("dota2", "dota.sh")


class DotaLauncher:
    """Check installation and launch Dota 2."""

    def __init__(self, config: SteamConfig):
        self.config = config

    def check_installed(self) -> Path:
        """Return Dota 2 install path or raise Dota2NotInstalledError."""
        install_dir = steam_utils.find_dota2_install(self.config)
        if install_dir is None:
            raise Dota2NotInstalledError(
                "Dota 2 не найдена на диске. Установите игру через Steam "
                "(или укажите правильный путь к Steam в настройках) перед запуском."
            )
        logger.info("Dota 2 найдена: %s", install_dir)
        return install_dir

    def is_running(self) -> bool:
        """Return True when a Dota 2 process is active."""
        for proc in psutil.process_iter(attrs=["name"]):
            name = (proc.info.get("name") or "").lower()
            if any(hint in name for hint in _PROCESS_NAME_HINTS):
                return True
        return False

    def launch(self, extra_launch_args: Optional[str] = None) -> None:
        """Launch Dota 2 on Steam. Raise Dota2NotInstalledError, if donot installed.
        
        extra_launch_args can be passed through steam://run/<appid>//<args>.
        """
        self.check_installed()

        if self.is_running():
            logger.info("Dota 2 уже запущена, повторный запуск не требуется.")
            return

        args = extra_launch_args if extra_launch_args is not None else self.config.launch_extra_args
        url = f"steam://run/{self.config.dota_app_id}"
        if args:
            url = f"{url}//{args}"

        logger.info("Запускаю Dota 2 через Steam: %s", url)
        self._open_steam_url(url)

    @staticmethod
    def _open_steam_url(url: str) -> None:
        if sys.platform == "win32":
            import os

            os.startfile(url)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            # On Linux, Steam typically registers the steam:// URI opener.
            try:
                subprocess.Popen(["xdg-open", url])
            except FileNotFoundError:
                webbrowser.open(url)