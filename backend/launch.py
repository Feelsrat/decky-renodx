"""Structured launch options. The frontend merges these into Steam's launch options.

The backend never writes Steam's config files; Steam owns them and would
overwrite our edits. Only ``SteamClient.Apps.SetAppLaunchOptions`` is used.
"""
from __future__ import annotations

from typing import Any

from .config import HDR_ENV


def spec(hook: str = "", *, args: list[str] | None = None, wrapper: list[str] | None = None) -> dict[str, Any]:
    overrides = {hook: "n,b"} if hook else {}
    data: dict[str, Any] = {
        "env": dict(HDR_ENV),
        "dll_overrides": overrides,
        "args": list(args or []),
        "wrapper": list(wrapper or []),
    }
    data["preview"] = preview(data)
    return data


def preview(data: dict[str, Any]) -> str:
    """Human-readable launch options for a spec when nothing else is set."""
    parts = [f"{key}={value}" for key, value in data.get("env", {}).items()]
    overrides = ";".join(f"{dll}={mode}" for dll, mode in data.get("dll_overrides", {}).items())
    if overrides:
        parts.append(f'WINEDLLOVERRIDES="{overrides}"')
    parts += [quote(item) for item in data.get("wrapper", [])]
    parts.append("%command%")
    parts += data.get("args", [])
    return " ".join(parts)


def quote(value: str) -> str:
    """Double-quote for Steam's shell, matching the frontend's quoting."""
    if value and all(ch.isalnum() or ch in "-_./=:%+," for ch in value):
        return value
    escaped = "".join("\\" + ch if ch in '"\\$`' else ch for ch in value)
    return f'"{escaped}"'
