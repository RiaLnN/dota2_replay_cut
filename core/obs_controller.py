"""OBS WebSocket wrapper for replay buffer control."""

from __future__ import annotations

import logging
import queue
import time
from pathlib import Path
from typing import Any, Optional

import obsws_python as obs

from config.settings import OBSConfig
from core.exceptions import OBSConnectionError, OBSReplayBufferError

logger = logging.getLogger(__name__)


def _first_attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Return the first existing attribute from provided names."""
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


class OBSController:
    """Manage OBS connection and replay buffer operations."""

    def __init__(self, config: OBSConfig):
        self.config = config
        self._req: Optional[obs.ReqClient] = None
        self._events: Optional[obs.EventClient] = None
        self._saved_path_queue: "queue.Queue[str]" = queue.Queue()

    # Connection.

    def connect(self) -> None:
        """Connect to OBS and raise OBSConnectionError on failure."""
        try:
            self._req = obs.ReqClient(
                host=self.config.host,
                port=self.config.port,
                password=self.config.password,
                timeout=self.config.connect_timeout_sec,
            )
            version = self._req.get_version()
            logger.info(
                "Подключение к OBS установлено (OBS %s, WebSocket %s)",
                _first_attr(version, "obs_version", default="?"),
                _first_attr(version, "obs_web_socket_version", default="?"),
            )

            self._events = obs.EventClient(
                host=self.config.host,
                port=self.config.port,
                password=self.config.password,
                timeout=self.config.connect_timeout_sec,
            )
            self._events.callback.register(self._on_replay_buffer_saved)
        except Exception as exc:  # noqa: BLE001 - OBS client may raise different socket exceptions
            raise OBSConnectionError(
                f"Не удалось подключиться к OBS по адресу {self.config.host}:{self.config.port}. "
                f"Проверьте, что OBS запущен и сервер WebSocket включён. Причина: {exc}"
            ) from exc

    def disconnect(self) -> None:
        for client in (self._req, self._events):
            if client is not None:
                try:
                    client.disconnect()
                except Exception:  # noqa: BLE001 - на закрытии соединения ошибки не критичны
                    pass
        self._req = None
        self._events = None

    @property
    def is_connected(self) -> bool:
        return self._req is not None

    # Replay buffer.

    def set_replay_buffer_duration(self, seconds: int) -> None:
        """Set OBS replay buffer duration when profile parameters allow it."""
        self._require_connected()
        for category in ("SimpleOutput", "AdvOut"):
            try:
                self._req.set_profile_parameter(category, "RecRBTime", str(seconds)) # type: ignore
                logger.info("Длительность буфера повторов OBS выставлена в %d сек (%s)", seconds, category)
                return
            except Exception as exc:  # noqa: BLE001 - continue with next category
                logger.debug("Не удалось задать RecRBTime в категории %s: %s", category, exc)

        logger.warning(
            "Не удалось автоматически выставить длительность буфера повторов (%d сек). "
            "Проверьте вручную: Настройки OBS -> Вывод -> Буфер повторов -> Макс. время повтора.",
            seconds,
        )

    def ensure_replay_buffer_active(self) -> None:
        """Start replay buffer if it is not active."""
        self._require_connected()
        try:
            status = self._req.get_replay_buffer_status() # type: ignore
            active = bool(_first_attr(status, "output_active", "outputActive", default=False))
        except Exception as exc:  # noqa: BLE001
            raise OBSReplayBufferError(f"Не удалось получить статус буфера повторов: {exc}") from exc

        if active:
            logger.info("Буфер повторов OBS уже активен.")
            return

        try:
            self._req.start_replay_buffer() # type: ignore
            logger.info("Буфер повторов OBS запущен.")
        except Exception as exc:  # noqa: BLE001
            raise OBSReplayBufferError(
                "Не удалось запустить буфер повторов. Убедитесь, что в настройках OBS "
                "включена опция 'Enable Replay Buffer' и назначена горячая клавиша "
                f"'Save Replay'. Причина: {exc}"
            ) from exc

    def stop_replay_buffer(self) -> None:
        if not self.is_connected:
            return
        try:
            self._req.stop_replay_buffer() # type: ignore
            logger.info("Буфер повторов OBS остановлен.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось остановить буфер повторов: %s", exc)

    def save_replay_buffer_and_wait(self, timeout_sec: float) -> Path:
        """Save replay buffer and return file path."""
        self._require_connected()

        # Clear stale replay-save events before requesting a new save.
        while not self._saved_path_queue.empty():
            self._saved_path_queue.get_nowait()

        try:
            self._req.save_replay_buffer() # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise OBSReplayBufferError(f"Не удалось запросить сохранение буфера повторов: {exc}") from exc

        try:
            path_str = self._saved_path_queue.get(timeout=timeout_sec)
            return Path(path_str)
        except queue.Empty:
            pass

        logger.debug("Событие ReplayBufferSaved не пришло за %.0f сек, пробую опросить OBS напрямую.", timeout_sec)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                resp = self._req.get_last_replay_buffer_replay() # type: ignore
                path_str = _first_attr(resp, "saved_replay_path", "savedReplayPath")
                if path_str:
                    return Path(path_str)
            except Exception as exc:  # noqa: BLE001
                logger.debug("GetLastReplayBufferReplay пока недоступен: %s", exc)
            time.sleep(0.5)

        raise OBSReplayBufferError(
            "OBS не сообщил путь сохранённого фрагмента буфера повторов вовремя."
        )

    # Internals.

    def _on_replay_buffer_saved(self, data: Any) -> None:
        path_str = _first_attr(data, "saved_replay_path", "savedReplayPath")
        if path_str:
            logger.debug("Получено событие ReplayBufferSaved: %s", path_str)
            self._saved_path_queue.put(path_str)

    def _require_connected(self) -> None:
        if self._req is None:
            raise OBSConnectionError("Нет активного соединения с OBS — вызовите connect() перед использованием.")