import json
import logging
from typing import Any, Dict, Optional
import os

_log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "max_queue_size": 500,
    "max_track_duration": 10800,  # 3 hours
    "dj_role_id": None,
    "volume_limit": 100
}

class SettingsManager:
    def __init__(self, file_path: str = "settings.json"):
        self.file_path = file_path
        self._settings: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.file_path):
            self._settings = {}
            return

        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
                # Convert keys to strings (JSON does this anyway, but just to be safe with internal dict)
                self._settings = {str(k): v for k, v in data.items()}
        except Exception as e:
            _log.error(f"Failed to load settings from {self.file_path}: {e}")
            self._settings = {}

    def _save(self) -> None:
        try:
            with open(self.file_path, "w") as f:
                json.dump(self._settings, f, indent=4)
        except Exception as e:
            _log.error(f"Failed to save settings to {self.file_path}: {e}")

    def get(self, guild_id: int, key: str) -> Any:
        guild_key = str(guild_id)
        if guild_key not in self._settings:
            return DEFAULT_SETTINGS.get(key)

        return self._settings[guild_key].get(key, DEFAULT_SETTINGS.get(key))

    def set(self, guild_id: int, key: str, value: Any) -> None:
        guild_key = str(guild_id)
        if guild_key not in self._settings:
            self._settings[guild_key] = {}

        self._settings[guild_key][key] = value
        self._save()

    def get_all(self, guild_id: int) -> Dict[str, Any]:
        """Returns effective settings for a guild (merging defaults)"""
        guild_key = str(guild_id)
        settings = DEFAULT_SETTINGS.copy()
        if guild_key in self._settings:
            settings.update(self._settings[guild_key])
        return settings
