#!/usr/bin/env python3
"""GUI entry point. Usage:
    python run_gui.py                 # opens with an empty workspace
    python run_gui.py <robot_folder>  # preload a given robot main folder

Requires PySide6:  python -m pip install PySide6-Essentials

Fieldbus Config Editor — © 2026 inasoft GmbH <https://www.inasoft.ch>
This program is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License v3 as published by the Free Software
Foundation. It is distributed WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU GPL
v3 (LICENSE) for details. "inasoft" and the inasoft logo are trademarks of
inasoft GmbH (see TRADEMARKS.md).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
