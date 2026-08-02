# Dota 2 Replay Cut

Automation toolkit for downloading Dota 2 replays, opening them in the game client, and saving highlight clips through OBS Replay Buffer.

The project supports two workflows:
- **Full Watch (`full_watch`)**: continuously watch a replay and auto-save clips when hero kills are detected from KDA OCR.
- **Timeline Jumps (`timeline_jumps`)**: jump to specific in-game timestamps, refine position with clock OCR, and save clips around those moments.

## Features

- Replay discovery and download via OpenDota API.
- Automatic replay launch in Dota 2 through Steam.
- OBS WebSocket integration for replay buffer control and clip saving.
- OCR-based kill detection from the in-game KDA block.
- OCR-based timeline correction using the in-game clock.
- Multi-match queue runner with per-job mode selection.

## Repository Structure

```text
config/
  settings.py             # Dataclass-based application configuration
core/
  replay_service.py       # OpenDota lookup + replay download/unpack
  dota_launcher.py        # Dota 2 launch and process checks
  game_input.py           # Console command input automation
  replay_player.py        # Replay load and timeline jump commands
  obs_controller.py       # OBS WebSocket wrapper
  kill_watcher.py         # KDA OCR kill detection and clip saves
  timeline_recorder.py    # Timeline-based clip extraction with OCR correction
  queue_runner.py         # Sequential replay job runner
utils/
  config_manager.py       # JSON config read/write helper
  logging_setup.py        # Console + rotating file logger setup
  time_utils.py           # Time/tick conversion helpers
main.py                   # Example entry point and queue definition
```

## Requirements

### Runtime

- Python 3.10+ (recommended)
- Steam with Dota 2 installed
- OBS Studio with **Replay Buffer enabled**
- OBS WebSocket (OBS 28+ includes it)
- Tesseract OCR installed and available via `PATH` or explicit `tesseract_cmd`

### Python dependencies

The code imports these external packages:
- `requests`
- `obsws-python`
- `opencv-python`
- `numpy`
- `mss`
- `pytesseract`
- `Pillow`
- `psutil`
- `vdf` (optional, with built-in fallback parser)

Windows-only automation dependencies (for game window input automation):
- `pydirectinput`
- `pygetwindow`
- `pyperclip`

## Installation

1. Clone the repository.
2. Create and activate a virtual environment.
3. Install dependencies (example):

```bash
pip install requests obsws-python opencv-python numpy mss pytesseract Pillow psutil vdf pydirectinput pygetwindow pyperclip
```

## Configuration

Configuration is defined by `AppConfig` in `config/settings.py`.

Important sections:
- `steam`: Steam path and Dota launch options.
- `replay`: OpenDota API settings and replay download behavior.
- `spectator`: in-game console command templates and timing.
- `timeline`: jump and OCR correction behavior.
- `obs`: OBS connection and replay buffer settings.
- `kda_ocr`: KDA OCR capture region and parsing settings.
- `output`: output directory and clip name templates.

The example `build_config()` in `main.py` shows how to set:
- Steam path
- Tesseract executable path
- OCR regions
- OBS host/port/password
- Output directory

## Usage

`main.py` is an example script that:
- Builds an `AppConfig`
- Defines a list of `ReplayJob` entries
- Runs jobs through `ReplayQueueRunner`

Run:

```bash
python main.py
```

### Replay job modes

`ReplayJob` supports:
- `match_id`: Dota 2 match ID
- `mode`: `PlaybackMode.FULL_WATCH` or `PlaybackMode.TIMELINE_JUMPS`
- `spectator_index`: hero index to follow
- `timelines`: list like `["20:00", "30:00"]` (for timeline mode)
- `lead_in_seconds`: clip lead-in override
- `post_match_buffer_sec`: extra wait after match end (full watch mode)

## How the pipeline works

1. Fetch match metadata from OpenDota.
2. Download and unpack replay (`.dem`) into Dota `replays` directory.
3. Launch Dota 2 (if not running).
4. Load replay via `playdemo`.
5. Depending on mode:
   - **Full Watch**: monitor KDA and save clip for each new kill.
   - **Timeline Jumps**: jump to target ticks, refine position via clock OCR, wait for OBS buffer, then save.
6. Move saved clips into the configured output folder with template-based names.

## Notes and limitations

- Automatic game input is implemented for Windows (`pydirectinput`/`pygetwindow`/`pyperclip`).
- If OCR regions are not configured correctly, kill/clock detection will fail.
- Replay availability depends on Valve retention and OpenDota data freshness.
- Some log messages are currently in Russian.

## Error handling

Custom exceptions in `core/exceptions.py` cover common failure groups:
- Replay lookup/download issues
- Dota installation and game window detection issues
- OBS connection/replay buffer issues

Queue runner callbacks (`on_job_started`, `on_job_finished`, `on_error`) can be used to integrate custom monitoring or UI.
