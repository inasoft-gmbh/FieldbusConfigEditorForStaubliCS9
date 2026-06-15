"""Native folder selection dialog via tkinter (built-in). Using only the dialog
does NOT commit the app to a tkinter GUI - the rest stays console-based.
Falls back to a typed path if no display/tk is available.
"""
from __future__ import annotations


def pick_folder(title: str = "Select the robot main folder") -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title=title)
        root.destroy()
        return path or None
    except Exception:
        try:
            return input(f"{title} (type path): ").strip() or None
        except EOFError:
            return None
