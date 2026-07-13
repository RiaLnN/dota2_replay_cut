"""Time conversion helpers for replay timeline calculations."""

from __future__ import annotations


def time_str_to_seconds(time_str: str) -> int:
    """Convert a string in MM:SS or H:MM:SS format to game seconds."""
    parts = [int(p) for p in time_str.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        raise ValueError(f"Invalid time format: {time_str!r}.")


def get_replay_tick(time_str: str, tick_rate: int = 30, pregame_offset_seconds: int = 90) -> int:
    """Convert game time to rough replay ticks with pregame offset."""
    target_seconds = time_str_to_seconds(time_str)
    
    # Apply pregame offset before converting to replay ticks.
    actual_seconds = target_seconds + pregame_offset_seconds

    return actual_seconds * tick_rate


def ticks_to_time_label(ticks: int, tick_rate: int = 30, pregame_offset_seconds: int = 90) -> str:
    """Convert replay ticks back to an MM:SS label."""
    total_seconds = ticks // tick_rate
    game_seconds = total_seconds - pregame_offset_seconds
    if game_seconds < 0:
        game_seconds = 0 
        
    minutes, seconds = divmod(game_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"