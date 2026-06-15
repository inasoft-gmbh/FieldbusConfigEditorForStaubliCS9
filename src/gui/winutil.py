"""Small Windows-specific UI helpers."""
from __future__ import annotations
import sys


def dark_titlebar(widget):
    """Ask Windows to draw this window's title bar in dark mode (DWM API). The
    frame/caption is OS-drawn, not Qt-styled, so this is the only way to darken
    it. No-op on non-Windows or older builds that lack the attribute."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        val = ctypes.c_int(1)
        for attr in (20, 19):      # DWMWA_USE_IMMERSIVE_DARK_MODE (20, or 19 pre-20H1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass
