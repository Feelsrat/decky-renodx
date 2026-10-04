"""Structured launch options. The frontend merges these into Steam's launch options.

The backend never writes Steam's config files; Steam owns them and would
overwrite our edits. Only ``SteamClient.Apps.SetAppLaunchOptions`` is used, and
the string itself is built in one place: src/utils/launchOptions.ts.
"""
from __future__ import annotations

from typing import Any

from .config import HDR_ENV


def spec(hook: str = "", *, args: list[str] | None = None, extra_dlls: list[str] | None = None) -> dict[str, Any]:
    dlls = [name for name in [hook, *(extra_dlls or [])] if name]
    return {
        "env": dict(HDR_ENV),
        "dll_overrides": {name: "n,b" for name in dlls},
        "args": list(args or []),
        "wrapper": [],
    }
