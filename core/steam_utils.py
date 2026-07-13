"""Steam and Dota 2 installation path discovery helpers."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import List, Optional

from config.settings import SteamConfig

logger = logging.getLogger(__name__)

try:
    import vdf  # type: ignore
except ImportError:  # pragma: no cover - optional dependency with fallback parser
    vdf = None


def _default_windows_steam_paths() -> List[Path]:
    return [
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
    ]


def _default_linux_steam_paths() -> List[Path]:
    home = Path.home()
    return [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
    ]


def get_steam_install_path(config: SteamConfig) -> Optional[Path]:
    """Return Steam install path or None when not found."""
    if config.steam_install_path:
        path = Path(config.steam_install_path)
        if path.exists():
            return path
        logger.warning("Путь Steam из конфигурации не существует: %s", path)

    if sys.platform == "win32":
        path = _read_steam_path_from_registry()
        if path and path.exists():
            return path
        candidates = _default_windows_steam_paths()
    else:
        candidates = _default_linux_steam_paths()

    for candidate in candidates:
        if candidate.exists():
            return candidate

    logger.error("Не удалось автоматически найти установку Steam.")
    return None


def _read_steam_path_from_registry() -> Optional[Path]:
    """Read Steam path from Windows registry."""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # Available only on Windows.

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            return Path(value)
    except OSError as exc:
        logger.debug("Не удалось прочитать путь Steam из реестра: %s", exc)
        return None


def get_library_folders(steam_path: Path) -> List[Path]:
    """Return all Steam library folders including the primary install path."""
    libraries = [steam_path]
    vdf_path = steam_path / "steamapps" / "libraryfolders.vdf"
    if not vdf_path.exists():
        logger.warning("Файл %s не найден, буду искать игру только в основной папке Steam.", vdf_path)
        return libraries

    try:
        data = _parse_library_folders_vdf(vdf_path)
    except Exception as exc:  # noqa: BLE001 - file may be corrupted or use unsupported format
        logger.warning("Не удалось разобрать %s: %s", vdf_path, exc)
        return libraries

    root = data.get("libraryfolders", data)
    for key, entry in root.items():
        if not isinstance(entry, dict):
            continue
        path_str = entry.get("path")
        if path_str:
            path = Path(path_str)
            if path not in libraries:
                libraries.append(path)
    return libraries


def _parse_library_folders_vdf(vdf_path: Path) -> dict:
    """Parse libraryfolders.vdf using vdf package or fallback parser."""
    text = vdf_path.read_text(encoding="utf-8", errors="ignore")
    if vdf is not None:
        return vdf.loads(text)
    return _naive_vdf_parse(text)


def _naive_vdf_parse(text: str) -> dict:
    """Minimal VDF parser used when the vdf package is unavailable."""
    import re

    result: dict = {}
    stack = [result]
    token_re = re.compile(r'"([^"]*)"\s*"([^"]*)"|"([^"]*)"|(\{)|(\})')
    for match in token_re.finditer(text):
        key_only = match.group(3)
        brace_close = match.group(5)
        if match.group(1) is not None:
            stack[-1][match.group(1)] = match.group(2)
        elif key_only is not None:
            new_dict: dict = {}
            stack[-1][key_only] = new_dict
            stack.append(new_dict)
        elif brace_close:
            if len(stack) > 1:
                stack.pop()
    return result


def find_dota2_install(config: SteamConfig) -> Optional[Path]:
    """Search Dota 2 install directory in all Steam libraries."""
    steam_path = get_steam_install_path(config)
    if steam_path is None:
        return None

    for library in get_library_folders(steam_path):
        manifest = library / "steamapps" / f"appmanifest_{config.dota_app_id}.acf"
        install_dir = library / config.dota_install_subpath
        if manifest.exists() and install_dir.exists():
            return install_dir
        if install_dir.exists():
            # Install directory may still exist even when manifest is missing.
            return install_dir

    return None


def is_dota2_installed(config: SteamConfig) -> bool:
    """Return True when Dota 2 install directory is found."""
    return find_dota2_install(config) is not None


def get_dota_replays_dir(config: SteamConfig) -> Optional[Path]:
    """Return path to Dota 2 replays directory used for .dem files."""
    install_dir = find_dota2_install(config)
    if install_dir is None:
        return None
    replays_dir = install_dir / "game" / "dota" / "replays"
    replays_dir.mkdir(parents=True, exist_ok=True)
    return replays_dir


def get_dota_executable(config: SteamConfig) -> Optional[Path]:
    """Return Dota 2 executable path for diagnostics."""
    install_dir = find_dota2_install(config)
    if install_dir is None:
        return None
    if sys.platform == "win32":
        exe = install_dir / "game" / "bin" / "win64" / "dota2.exe"
    else:
        exe = install_dir / "game" / "dota.sh"
    return exe if exe.exists() else None