#!/usr/bin/env python3
"""Entry point. Usage:
    python run.py                 # opens a folder picker
    python run.py <robot_folder>  # analyze a given robot main folder
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from cli.app import main

if __name__ == "__main__":
    raise SystemExit(main())
