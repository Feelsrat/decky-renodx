import os
import json
import time
from datetime import datetime, timedelta

METADATA_SCHEMA_VERSION = 2

class PersistentCache:
    def __init__(self, cache_file: str):
        self.cache_file = os.path.expanduser(cache_file)
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(self.data, f, indent=2)

    def get(self, key: str, expiry_days: int = 7):
        """Get data from cache if not expired."""
        entry = self.data.get(key)
        if not entry:
            return None
            
        # Check expiry
        cached_time = datetime.fromisoformat(entry.get("timestamp", "2000-01-01T00:00:00"))
        if datetime.now() > cached_time + timedelta(days=expiry_days):
            return None
            
        return entry.get("value")

    def set(self, key: str, value: any):
        """Set data in cache with current timestamp."""
        self.data[key] = {
            "value": value,
            "timestamp": datetime.now().isoformat()
        }
        self._save()

    def get_game_metadata(self, appid: str):
        metadata = self.get(f"metadata_{appid}", expiry_days=14)
        if not isinstance(metadata, dict):
            return None
        if metadata.get("schema_version") != METADATA_SCHEMA_VERSION:
            return None
        return metadata

    def set_game_metadata(self, appid: str, metadata: dict):
        metadata = dict(metadata)
        metadata["schema_version"] = METADATA_SCHEMA_VERSION
        if metadata.get("graphics_api") == "unknown":
            metadata.pop("graphics_api", None)
        self.set(f"metadata_{appid}", metadata)

    def set_game_metadata_value(self, appid: str, key: str, value):
        metadata = self.get_game_metadata(appid) or {}
        metadata[key] = value
        self.set_game_metadata(appid, metadata)

    def get_api_info(self, exe_path: str):
        # Cache API info per file path + mtime to handle updates
        if not os.path.exists(exe_path):
            return None
        mtime = os.path.getmtime(exe_path)
        key = f"api_{exe_path}_{mtime}"
        return self.get(key, expiry_days=30)

    def set_api_info(self, exe_path: str, info: dict):
        if info.get("api") == "unknown":
            return
        mtime = os.path.getmtime(exe_path)
        key = f"api_{exe_path}_{mtime}"
        self.set(key, info)

    def clear(self):
        self.data = {}
        self._save()
