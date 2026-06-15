"""Persisted user preferences (remembered between runs), e.g. the numbering
scheme and last-used add mode. Stored as settings.json in the app root.
"""
from __future__ import annotations
import json
from pathlib import Path
from .paths import appdata_dir

_PATH = appdata_dir() / "settings.json"

_DEFAULTS = {
    "naming": {"start": 0, "digits": 1},   # numbering start value and zero-pad width
    "last_mode": "single",                  # 'single' | 'array'
    "last_type": "word",
    "last_folder": "",                      # robot folder to reopen on startup
    "last_project": "",                     # .spj of the project chosen there
}


def load() -> dict:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    merged = {**_DEFAULTS, **data}
    merged["naming"] = {**_DEFAULTS["naming"], **data.get("naming", {})}
    return merged


def save(data: dict) -> None:
    try:
        _PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass
