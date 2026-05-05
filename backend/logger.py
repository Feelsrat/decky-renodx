import os
import logging
from pathlib import Path

def setup_per_game_logger(appid: str, logs_dir: str = "~/homebrew/logs/hdr-plugin"):
    """Set up a logger that writes to a per-game log file."""
    # Expand the logs directory path
    logs_dir = os.path.expanduser(logs_dir)
    os.makedirs(logs_dir, exist_ok=True)
    
    log_file = os.path.join(logs_dir, f"{appid}.log")
    
    logger = logging.getLogger(f"HDR_{appid}")
    logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers if any
    if logger.hasHandlers():
        logger.handlers.clear()
        
    # File handler
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(fh)
    
    return logger

def get_game_log_path(appid: str, logs_dir: str = "~/homebrew/logs/hdr-plugin") -> str:
    """Return the absolute path to the game's log file."""
    return os.path.join(os.path.expanduser(logs_dir), f"{appid}.log")
