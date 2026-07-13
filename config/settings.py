"""Application configuration schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict


@dataclass
class ScreenRegion:
    """Rectangular screen area in physical pixels."""

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def is_configured(self) -> bool:
        """Return True when region size is valid."""
        return self.width > 0 and self.height > 0

    def as_mss_dict(self) -> Dict[str, int]:
        """Return region format expected by mss."""
        return {"left": self.x, "top": self.y, "width": self.width, "height": self.height}


@dataclass
class SteamConfig:
    """Steam and Dota 2 installation settings."""

    steam_install_path: str = ""  # Empty value enables automatic Steam path detection.
    dota_app_id: int = 570
    dota_install_subpath: str = "steamapps/common/dota 2 beta"
    launch_extra_args: str = "-console"  # Additional launch arguments.


@dataclass
class ReplayConfig:
    """Replay discovery and download settings."""

    opendota_base_url: str = "https://api.opendota.com/api"
    opendota_api_key: str = ""  # Optional API key for higher request limits.
    max_replay_age_days: int = 14
    request_parse_if_missing: bool = True
    parse_poll_interval_sec: float = 3.0
    parse_poll_timeout_sec: float = 90.0
    download_timeout_sec: float = 120.0
    download_chunk_size: int = 65536


@dataclass
class SpectatorConfig:
    """Replay launch and spectator control commands."""

    playdemo_command_template: str = "playdemo replays/{demo_name}"
    spectator_mode_command: str = "dota_spectator_mode 2"  # 2 means Hero Chase mode.
    follow_hero_command_template: str = "dota_spectator_hero_index {index}"
    go_to_timeline_command_template: str = "demo_goto {timeline} relative"
    timeline_enable: bool = False  # Legacy option retained for compatibility.
    console_toggle_key: str = "`"
    window_appear_timeout_sec: float = 60.0
    post_launch_wait_sec: float = 30.0
    post_demo_load_wait_sec: float = 10.0
    command_send_delay_sec: float = 0.4
    auto_send_console_commands: bool = True


@dataclass
class TimelineConfig:
    """Replay timeline-jump and OCR correction settings."""

    tick_rate: int = 30
    default_lead_in_seconds: int = 20
    goto_is_relative: bool = True
    resend_follow_hero_after_jump: bool = True
    post_jump_wait_extra_sec: float = 1.0
    pregame_offset_seconds: int = 90
    clock_region: ScreenRegion = field(
        default_factory=lambda: ScreenRegion(x=1240, y=27, width=80, height=23)
    )
    clock_ocr_attempts: int = 3
    clock_ocr_retry_delay_sec: float = 0.35
    max_correction_attempts: int = 3
    correction_tolerance_seconds: int = 1
    max_single_correction_seconds: int = 90
    post_correction_settle_sec: float = 1.5
    post_rough_jump_settle_sec: float = 2.0
    pause_detect_gap_sec: int = 120
    max_pregame_and_pause_allowance_seconds: int = 1200
    pause_max_wait_sec: float = 90.0


@dataclass
class OBSConfig:
    """OBS WebSocket and replay buffer settings."""

    host: str = "localhost"
    port: int = 4455
    password: str = ""
    connect_timeout_sec: float = 5.0
    pre_kill_buffer_seconds: int = 15  # Number of seconds before a kill to keep.
    auto_set_buffer_duration: bool = True
    save_confirm_timeout_sec: float = 15.0


@dataclass
class KDAOcrConfig:
    """KDA OCR capture and parsing settings."""

    region: ScreenRegion = field(default_factory=ScreenRegion)
    poll_interval_sec: float = 0.5
    tesseract_cmd: str = ""  # Path to tesseract executable when not available in PATH.
    ocr_upscale_factor: float = 3.0
    required_consistent_reads: int = 2
    max_reasonable_kill_value: int = 99


@dataclass
class OutputConfig:
    """Output file and naming settings."""

    output_root_dir: str = "./output"
    kill_clip_name_template: str = "{index}_kill{ext}"  # {index} is kill number, {ext} is OBS file extension.
    timeline_clip_name_template: str = "{index}_{label}{ext}"  # {label} is timeline label, {ext} is OBS file extension.


@dataclass
class AppConfig:
    """Top-level application configuration object."""

    steam: SteamConfig = field(default_factory=SteamConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    spectator: SpectatorConfig = field(default_factory=SpectatorConfig)
    timeline: TimelineConfig = field(default_factory=TimelineConfig)
    obs: OBSConfig = field(default_factory=OBSConfig)
    kda_ocr: KDAOcrConfig = field(default_factory=KDAOcrConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "AppConfig":
        """Build AppConfig from nested dictionary values."""
        def _region(d: Dict[str, Any]) -> ScreenRegion:
            return ScreenRegion(**{**asdict(ScreenRegion()), **(d or {})})

        def _section(cls, d: Dict[str, Any]):
            defaults = asdict(cls())
            defaults.update(d or {})
            # ScreenRegion is the only nested second-level config type.
            if cls is KDAOcrConfig:
                defaults["region"] = _region(defaults.get("region", {}))
                return KDAOcrConfig(**defaults)
            if cls is TimelineConfig:
                defaults["clock_region"] = _region(defaults.get("clock_region", {}))
                return TimelineConfig(**defaults)
            return cls(**defaults)

        return AppConfig(
            steam=_section(SteamConfig, data.get("steam", {})), # type: ignore
            replay=_section(ReplayConfig, data.get("replay", {})), # type: ignore
            spectator=_section(SpectatorConfig, data.get("spectator", {})), # type: ignore
            timeline=_section(TimelineConfig, data.get("timeline", {})), # type: ignore
            obs=_section(OBSConfig, data.get("obs", {})), # type: ignore
            kda_ocr=_section(KDAOcrConfig, data.get("kda_ocr", {})), # type: ignore
            output=_section(OutputConfig, data.get("output", {})), # type: ignore
        )