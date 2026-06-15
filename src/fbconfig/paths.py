"""Where the app keeps writable data (settings.json, saved templates).

Normal run  -> the project root (next to settings.json as before).
Frozen .exe -> the folder that contains the .exe (portable: settings + templates
live next to the program, so copying the folder carries everything along).
"""
from __future__ import annotations
import sys
from pathlib import Path


def appdata_dir() -> Path:
    if getattr(sys, "frozen", False):                 # PyInstaller bundle
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]        # project root (src/fbconfig/..)


def asset(name: str) -> str:
    """Path to a bundled read-only asset (logo, icon). In a frozen build these
    live under the PyInstaller extraction dir (sys._MEIPASS/assets)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "assets"
    else:
        base = Path(__file__).resolve().parents[2] / "assets"
    return str(base / name)
