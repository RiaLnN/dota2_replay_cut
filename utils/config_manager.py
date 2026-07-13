"""Load and save application configuration in a JSON file."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import AppConfig

logger = logging.getLogger(__name__)


class ConfigManager:
    """Read and write AppConfig to a specific file path."""

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)

    def load(self) -> AppConfig:
        """Load configuration and create a default file when it does not exist."""
        if not self.config_path.exists():
            logger.info("Файл конфигурации %s не найден, создаю с настройками по умолчанию", self.config_path)
            default = AppConfig()
            self.save(default)
            return default

        try:
            raw_text = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            return AppConfig.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            logger.warning(
                "Не удалось прочитать %s (%s). Использую настройки по умолчанию.",
                self.config_path, exc,
            )
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        """Save configuration to disk as readable JSON."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
        self.config_path.write_text(text, encoding="utf-8")
        logger.debug("Конфигурация сохранена в %s", self.config_path)