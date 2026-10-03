"""Plugin-wide and per-game logging."""
from __future__ import annotations

import logging
import logging.handlers
import threading
from pathlib import Path

from . import fsutil

_plugin_logger = logging.getLogger("decky_renodx")
_game_loggers: dict[str, logging.Logger] = {}
_lock = threading.Lock()
_logs_dir: Path | None = None

FORMAT = "%(asctime)s %(levelname)s %(message)s"


def set_plugin_logger(logger: logging.Logger) -> None:
    global _plugin_logger
    _plugin_logger = logger


def plugin() -> logging.Logger:
    return _plugin_logger


def configure(logs_dir: Path) -> None:
    global _logs_dir
    _logs_dir = logs_dir


def game_log_path(appid: str) -> Path | None:
    if _logs_dir is None:
        return None
    return _logs_dir / f"{appid}.log"


def game(appid: str) -> logging.Logger:
    """Logger that writes to the plugin log and to logs/<appid>.log."""
    appid = str(appid)
    with _lock:
        logger = _game_loggers.get(appid)
        if logger is not None:
            return logger
        logger = logging.getLogger(f"decky_renodx.game.{appid}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.addHandler(_ForwardHandler())
        path = game_log_path(appid)
        if path is not None:
            try:
                fsutil.makedirs(path.parent)
                handler = _OwnedRotatingHandler(path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
                handler.setFormatter(logging.Formatter(FORMAT))
                logger.addHandler(handler)
                fsutil.chown(path)
            except OSError as error:
                _plugin_logger.warning("Could not open per-game log %s: %s", path, error)
        _game_loggers[appid] = logger
        return logger


def close_all() -> None:
    with _lock:
        for logger in _game_loggers.values():
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        _game_loggers.clear()


class _OwnedRotatingHandler(logging.handlers.RotatingFileHandler):
    """Keeps the log owned by the deck user after rollover (the launch wrapper appends to it)."""

    def doRollover(self) -> None:
        super().doRollover()
        fsutil.chown(self.baseFilename)
        fsutil.chown(f"{self.baseFilename}.1")


class _ForwardHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        appid = record.name.rsplit(".", 1)[-1]
        _plugin_logger.log(record.levelno, "[%s] %s", appid, record.getMessage())
