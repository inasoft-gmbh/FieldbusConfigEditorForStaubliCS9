"""GUI entry point. Sets up the dark theme and shows the main window."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ on path

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QPalette, QColor, QIcon

from gui import theme
from gui.main_window import MainWindow
from gui.winutil import dark_titlebar
from fbconfig.paths import asset


class _DarkTitleFilter(QObject):
    """Apply the dark title bar to every top-level window (dialogs too) on show."""
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            dark_titlebar(obj)
        return False


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    import fbconfig as fb
    app = QApplication(sys.argv[:1])
    app.setApplicationName(fb.APP_NAME)
    app.setStyle("Fusion")
    icon = QIcon(asset("inasoft_icon.png"))    # taskbar / window icon (one stroke)
    app.setWindowIcon(icon)
    # distinct AppUserModelID so Windows uses our icon in the taskbar (not python's)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "inasoft.FieldbusConfigEditorForStaubliCS9")
    except Exception:
        pass

    # base palette so native bits (tooltips, menus) match the QSS
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(theme.BG))
    pal.setColor(QPalette.Base, QColor(theme.BG))
    pal.setColor(QPalette.AlternateBase, QColor(theme.PANEL))
    pal.setColor(QPalette.Text, QColor(theme.TEXT))
    pal.setColor(QPalette.WindowText, QColor(theme.TEXT))
    pal.setColor(QPalette.Button, QColor(theme.PANEL))
    pal.setColor(QPalette.ButtonText, QColor(theme.TEXT))
    pal.setColor(QPalette.Highlight, QColor(theme.ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#04201d"))
    app.setPalette(pal)
    app.setStyleSheet(theme.QSS)
    app._dark_filter = _DarkTitleFilter()      # dark title bar for all windows
    app.installEventFilter(app._dark_filter)

    win = MainWindow(initial_folder=argv[0] if argv else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
