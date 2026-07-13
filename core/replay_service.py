"""OpenDota-based replay discovery and download service."""

from __future__ import annotations

import bz2
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

from config.settings import ReplayConfig
from core.exceptions import (
    MatchNotFoundError,
    ReplayDataUnavailableError,
    ReplayDownloadError,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


@dataclass
class MatchPlayer:
    """Minimal player data required for spectator selection."""

    player_slot: int
    hero_id: int
    hero_name: str
    account_id: Optional[int]
    is_radiant: bool
    position_in_team: int  # 0-4
    spectator_index: int  # Best guess for dota_spectator_hero_index (0-9).


@dataclass
class MatchInfo:
    """Match metadata required to download and play a replay."""

    match_id: int
    start_time: int
    duration: int
    cluster: Optional[int]
    replay_salt: Optional[int]
    replay_url: Optional[str]
    players: List[MatchPlayer] = field(default_factory=list)
    age_days: float = 0.0
    is_expired: bool = False

    def find_player(self, hero_id: int) -> Optional[MatchPlayer]:
        return next((p for p in self.players if p.hero_id == hero_id), None)


class ReplayService:
    """Handle OpenDota requests used for replay lookup and download."""

    def __init__(self, config: ReplayConfig):
        self.config = config
        self._session = requests.Session()
        self._hero_names_cache: Dict[int, str] = {}

    # Public methods.

    def get_match_info(self, match_id: int) -> MatchInfo:
        """Get match info and request parse when replay metadata is missing."""
        data = self._fetch_match(match_id)

        if not data.get("replay_salt"):
            if not self.config.request_parse_if_missing:
                raise ReplayDataUnavailableError(
                    f"Для матча {match_id} ещё нет данных о реплее (replay_salt), "
                    "а автоматический запрос разбора отключён в настройках."
                )
            logger.info("У матча %s пока нет данных о реплее, запрашиваю разбор у OpenDota...", match_id)
            self._request_parse_and_wait(match_id)
            data = self._fetch_match(match_id)

        return self._build_match_info(data)

    def download_replay(
        self,
        match_info: MatchInfo,
        dest_dir: Path,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> Path:
        """Download and unpack replay .dem file into dest_dir."""
        url = self._resolve_replay_url(match_info)
        dest_dir.mkdir(parents=True, exist_ok=True)

        compressed_path = dest_dir / f"{match_info.match_id}_{match_info.replay_salt}.dem.bz2"
        demo_path = dest_dir / f"{match_info.match_id}_{match_info.replay_salt}.dem"

        if demo_path.exists():
            logger.info("Реплей уже скачан ранее: %s", demo_path)
            return demo_path

        logger.info("Скачиваю реплей: %s", url)
        try:
            response = self._session.get(url, stream=True, timeout=self.config.download_timeout_sec)
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if status == 404:
                raise ReplayDownloadError(
                    "Реплей не найден на серверах Valve (HTTP 404). Обычно это значит, что "
                    "срок его хранения истёк — сервера Valve хранят реплеи ограниченное время."
                ) from exc
            raise ReplayDownloadError(f"Ошибка скачивания реплея: HTTP {status}") from exc
        except requests.exceptions.RequestException as exc:
            raise ReplayDownloadError(f"Сетевая ошибка при скачивании реплея: {exc}") from exc

        total = int(response.headers.get("Content-Length", 0))
        downloaded = 0
        with open(compressed_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=self.config.download_chunk_size):
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)

        logger.info("Распаковываю %s", compressed_path.name)
        try:
            self._decompress_bz2(compressed_path, demo_path)
        finally:
            compressed_path.unlink(missing_ok=True)

        return demo_path

    def get_hero_names(self, hero_ids: List[int]) -> Dict[int, str]:
        """Return localized hero names by ID with session-level caching."""
        missing = [hid for hid in hero_ids if hid not in self._hero_names_cache]
        if missing:
            url = f"{self.config.opendota_base_url}/heroes"
            response = self._session.get(url, params=self._auth_params(), timeout=15)
            response.raise_for_status()
            for hero in response.json():
                self._hero_names_cache[hero["id"]] = hero.get("localized_name", f"Hero {hero['id']}")
        return {hid: self._hero_names_cache.get(hid, f"Hero {hid}") for hid in hero_ids}

    # Internal methods.

    def _auth_params(self) -> dict:
        return {"api_key": self.config.opendota_api_key} if self.config.opendota_api_key else {}

    def _fetch_match(self, match_id: int) -> dict:
        url = f"{self.config.opendota_base_url}/matches/{match_id}"
        try:
            response = self._session.get(url, params=self._auth_params(), timeout=15)
        except requests.exceptions.RequestException as exc:
            raise MatchNotFoundError(f"Не удалось обратиться к OpenDota API: {exc}") from exc

        if response.status_code == 404:
            raise MatchNotFoundError(f"Матч с ID {match_id} не найден.")
        response.raise_for_status()
        data = response.json()
        if "match_id" not in data:
            raise MatchNotFoundError(f"OpenDota не вернул данные по матчу {match_id}.")
        return data

    def _request_parse_and_wait(self, match_id: int) -> None:
        url = f"{self.config.opendota_base_url}/request/{match_id}"
        try:
            response = self._session.post(url, params=self._auth_params(), timeout=15)
            response.raise_for_status()
            job = response.json()
        except requests.exceptions.RequestException as exc:
            raise ReplayDataUnavailableError(f"Не удалось запросить разбор реплея: {exc}") from exc

        job_id = job.get("job", {}).get("jobId") if isinstance(job, dict) else None
        deadline = time.monotonic() + self.config.parse_poll_timeout_sec

        while time.monotonic() < deadline:
            time.sleep(self.config.parse_poll_interval_sec)
            try:
                if job_id is not None:
                    status_resp = self._session.get(
                        f"{self.config.opendota_base_url}/request/{job_id}", timeout=15
                    )
                    if status_resp.status_code == 200 and not status_resp.json():
                        return  # Empty response indicates completion in OpenDota conventions.
                # Validate replay availability directly from match payload.
                data = self._fetch_match(match_id)
                if data.get("replay_salt"):
                    return
            except (MatchNotFoundError, requests.exceptions.RequestException):
                continue

        logger.warning(
            "Не дождался разбора реплея матча %s за %.0f сек. Попробую получить данные как есть.",
            match_id, self.config.parse_poll_timeout_sec,
        )

    def _resolve_replay_url(self, match_info: MatchInfo) -> str:
        if match_info.replay_url:
            return match_info.replay_url
        if match_info.cluster is not None and match_info.replay_salt is not None:
            return (
                f"http://replay{match_info.cluster}.valve.net/570/"
                f"{match_info.match_id}_{match_info.replay_salt}.dem.bz2"
            )
        raise ReplayDownloadError(
            "Недостаточно данных (cluster/replay_salt) для формирования ссылки на реплей."
        )

    def _build_match_info(self, data: dict) -> MatchInfo:
        match_id = data["match_id"]
        start_time = int(data.get("start_time", 0))
        duration = int(data.get("duration", 0))
        end_time = start_time + duration
        age_days = max(0.0, (time.time() - end_time) / 86400.0)

        players_raw = data.get("players", [])
        hero_ids = [p["hero_id"] for p in players_raw]
        hero_names = self.get_hero_names(hero_ids)

        players = [self._build_player(p, hero_names) for p in players_raw]
        players.sort(key=lambda p: p.spectator_index)

        return MatchInfo(
            match_id=match_id,
            start_time=start_time,
            duration=duration,
            cluster=data.get("cluster"),
            replay_salt=data.get("replay_salt"),
            replay_url=data.get("replay_url"),
            players=players,
            age_days=age_days,
            is_expired=age_days > self.config.max_replay_age_days,
        )

    @staticmethod
    def _build_player(raw: dict, hero_names: Dict[int, str]) -> MatchPlayer:
        player_slot = raw["player_slot"]
        is_radiant = player_slot < 128
        position = player_slot & 0x07
        # Map 0-4 to Radiant and 5-9 to Dire as a spectator index approximation.
        spectator_index = position if is_radiant else 5 + position
        return MatchPlayer(
            player_slot=player_slot,
            hero_id=raw["hero_id"],
            hero_name=hero_names.get(raw["hero_id"], f"Hero {raw['hero_id']}"),
            account_id=raw.get("account_id"),
            is_radiant=is_radiant,
            position_in_team=position,
            spectator_index=spectator_index,
        )

    @staticmethod
    def _decompress_bz2(src: Path, dest: Path) -> None:
        with bz2.open(src, "rb") as src_fh, open(dest, "wb") as dest_fh:
            while True:
                chunk = src_fh.read(1024 * 1024)
                if not chunk:
                    break
                dest_fh.write(chunk)