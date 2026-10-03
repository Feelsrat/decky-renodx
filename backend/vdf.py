"""Minimal parser for Valve's text KeyValues (VDF/ACF) format."""
from __future__ import annotations

from pathlib import Path
from typing import Any

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokens(text: str):
    i, length = 0, len(text)
    while i < length:
        char = text[i]
        if char.isspace():
            i += 1
        elif char == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = length if end < 0 else end + 1
        elif char in "{}":
            yield char
            i += 1
        elif char == '"':
            i += 1
            out = []
            while i < length and text[i] != '"':
                if text[i] == "\\" and i + 1 < length:
                    out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            i += 1
            yield ("str", "".join(out))
        else:
            start = i
            while i < length and not text[i].isspace() and text[i] not in '{}"':
                i += 1
            yield ("str", text[start:i])


def loads(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack = [root]
    key: str | None = None
    for token in _tokens(text):
        if token == "{":
            child: dict[str, Any] = {}
            stack[-1][key if key is not None else ""] = child
            stack.append(child)
            key = None
        elif token == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            value = token[1]
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    return root


def load(path: Path) -> dict[str, Any]:
    return loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def loads_binary(data: bytes) -> dict[str, Any]:
    """Parse Steam's binary KeyValues (shortcuts.vdf)."""
    pos = 0

    def cstring() -> str:
        nonlocal pos
        end = data.index(b"\0", pos)
        value = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return value

    def mapping() -> dict[str, Any]:
        nonlocal pos
        result: dict[str, Any] = {}
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == 0x08:
                return result
            key = cstring()
            if kind == 0x00:
                result[key] = mapping()
            elif kind == 0x01:
                result[key] = cstring()
            elif kind == 0x02:
                result[key] = int.from_bytes(data[pos:pos + 4], "little", signed=True)
                pos += 4
            elif kind == 0x03:  # float32; never needed, just skipped
                result[key] = None
                pos += 4
            elif kind == 0x07:
                result[key] = int.from_bytes(data[pos:pos + 8], "little", signed=False)
                pos += 8
            else:
                raise ValueError(f"Unsupported binary VDF type {kind:#x}")
        return result

    return mapping()


def load_binary(path: Path) -> dict[str, Any]:
    return loads_binary(Path(path).read_bytes())


def get(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Case-insensitive nested lookup."""
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        lowered = key.lower()
        current = next((value for name, value in current.items() if name.lower() == lowered), None)
        if current is None:
            return default
    return current
