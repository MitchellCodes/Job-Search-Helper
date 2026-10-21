"""Portable data location: a `data/` folder next to the app, never system folders."""
import os
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller build
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("JOBHELPER_DATA")
    path = Path(override) if override else app_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "jobhelper.sqlite"
