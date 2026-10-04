"""A JSON document fetched over HTTPS and cached on disk for a day."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import fsutil, log

CACHE_TTL = 24 * 3600
RETRY_AFTER = 15 * 60


class CachedJson:
    """Fetched at most daily; the stale copy is used when offline, and failures aren't retried
    for RETRY_AFTER so a slow network doesn't stall every panel refresh. ``get`` never raises."""

    def __init__(self, cache_file: Path, url: str, fetch_text: Callable[[str], str], valid: Callable[[Any], bool], label: str):
        self.cache_file = Path(cache_file)
        self.url = url
        self._fetch_text = fetch_text
        self._valid = valid
        self.label = label
        self._data: Any = None
        self._fetched_at = 0.0
        self._retry_after = 0.0
        self.version = 0  # bumped whenever the data changes, for callers that build indexes

    def get(self) -> Any:
        if self._data is None:
            cached = fsutil.read_json(self.cache_file, {}) or {}
            if "data" in cached and self._valid(cached["data"]):
                self._set(cached["data"], float(cached.get("fetched_at", 0)))
        now = time.time()
        if (self._data is not None and now - self._fetched_at < CACHE_TTL) or now < self._retry_after:
            return self._data
        try:
            data = json.loads(self._fetch_text(self.url))
            if not self._valid(data):
                raise ValueError("unexpected format")
            self._set(data, time.time())
            fsutil.write_json(self.cache_file, {"fetched_at": self._fetched_at, "data": data})
        except Exception as error:
            self._retry_after = time.time() + RETRY_AFTER
            log.plugin().warning("%s unavailable%s: %s", self.label, ", using cached copy" if self._data is not None else "", error)
        return self._data

    def _set(self, data: Any, fetched_at: float) -> None:
        self._data, self._fetched_at = data, fetched_at
        self.version += 1

    def clear(self) -> None:
        self._data, self._fetched_at, self._retry_after = None, 0.0, 0.0
        self.version += 1
        if self.cache_file.exists():
            self.cache_file.unlink()
