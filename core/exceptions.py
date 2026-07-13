"""
Application domain exceptions
"""

from __future__ import annotations


class Dota2RecorderError(Exception):
    """Basic Application Exception"""


class Dota2NotInstalledError(Dota2RecorderError):
    """Dota 2 not found on disk (game not downloaded/installed)"""


class SteamNotFoundError(Dota2RecorderError):
    """Could not find Steam installation on disk"""


class MatchNotFoundError(Dota2RecorderError):
    """The match with the specified ID was not found via the OpenDota API"""


class ReplayDataUnavailableError(Dota2RecorderError):
    """There is no replay data for this match yet (cluster/replay_salt)"""


class ReplayDownloadError(Dota2RecorderError):
    """Error downloading replay file (including if it is no longer stored on Valve servers)"""


class OBSConnectionError(Dota2RecorderError):
    """Unable to connect to OBS via obs-websocket"""


class OBSReplayBufferError(Dota2RecorderError):
    """Error while working with OBS replay buffer (buffer not enabled, not found, etc.)"""


class GameWindowNotFoundError(Dota2RecorderError):
    """Unable to find a running Dota 2 window to send commands to"""