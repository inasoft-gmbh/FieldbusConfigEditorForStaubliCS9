"""Non-modal toast / snackbar notifications.

A modern replacement for the informational QMessageBox: short messages slide in
from the bottom-right, stack, and auto-dismiss (slide back out + fade). Destructive
or robot-affecting confirmations stay modal — toasts are for feedback, not consent.
"""
from __future__ import annotations

from PySide6.QtCore import (Qt, QTimer, QPropertyAnimation, QParallelAnimationGroup,
                            QEasingCurve, QPoint, QObject)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                              QGraphicsOpacityEffect)

from . import theme

# kind -> (accent colour, glyph, lifetime in ms). All finite — even errors fade.
_KIND = {
    "info":    (theme.ACCENT, "ℹ", 3200),
    "success": (theme.OK,     "✓", 3600),
    "warn":    (theme.WARN,   "!", 6000),
    "error":   (theme.ERR,    "✕", 7000),
}
_SLIDE = 52                                  # px the toast travels on slide in/out


class Toast(QFrame):
    def __init__(self, manager: "ToastManager", text: str, kind: str, timeout: int):
        super().__init__(manager.host)
        self.manager = manager
        self._dismissing = False
        color, glyph, _ = _KIND.get(kind, _KIND["info"])
        self.setObjectName("Toast")
        self.setStyleSheet(
            f"#Toast {{ background: {theme.PANEL_HI};"
            f" border: 1px solid {color}; border-radius: 10px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 11, 8, 11)
        lay.setSpacing(11)

        ic = QLabel(glyph)
        ic.setAlignment(Qt.AlignCenter)
        ic.setFixedSize(22, 22)
        ic.setStyleSheet(
            f"color: #04201d; background: {color}; border-radius: 11px;"
            f" font-size: 13px; font-weight: 800;")
        lay.addWidget(ic, 0, Qt.AlignTop)

        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setMaximumWidth(330)
        msg.setStyleSheet("font-size: 12px;")
        msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(msg, 1)

        close = QPushButton("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(20, 20)
        close.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 0; color: {theme.TEXT_DIM};"
            f" font-size: 11px; }} QPushButton:hover {{ color: {theme.TEXT}; }}")
        close.clicked.connect(self.dismiss)
        lay.addWidget(close, 0, Qt.AlignTop)

        self.setMinimumWidth(300)
        self.setMaximumWidth(390)

        self._eff = QGraphicsOpacityEffect(self)
        self._eff.setOpacity(0.0)
        self.setGraphicsEffect(self._eff)
        self._fade = QPropertyAnimation(self._eff, b"opacity", self)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._grp = QParallelAnimationGroup(self)
        self._grp.addAnimation(self._fade)
        self._grp.addAnimation(self._slide)
        self._gone_connected = False

        if timeout:
            self._life = QTimer(self)
            self._life.setSingleShot(True)
            self._life.timeout.connect(self.dismiss)
            self._life.start(timeout)

    def _disconnect(self):
        if self._gone_connected:
            self._grp.finished.disconnect(self._gone)
            self._gone_connected = False

    def play_in(self, final: QPoint):
        """Slide DOWN from above + fade in to the final (top-centre) position."""
        self._grp.stop()
        self._disconnect()
        self.move(final.x(), final.y() - _SLIDE)
        self._fade.setDuration(240)
        self._fade.setStartValue(self._eff.opacity())
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.setDuration(300)
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(final)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._grp.start()

    def dismiss(self):
        if self._dismissing:
            return
        self._dismissing = True
        cur = self.pos()
        self._grp.stop()
        self._disconnect()
        self._fade.setDuration(220)
        self._fade.setStartValue(self._eff.opacity())
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.InCubic)
        self._slide.setDuration(220)
        self._slide.setStartValue(cur)
        self._slide.setEndValue(QPoint(cur.x(), cur.y() - _SLIDE))   # slide back up
        self._slide.setEasingCurve(QEasingCurve.InCubic)
        self._grp.finished.connect(self._gone)
        self._gone_connected = True
        self._grp.start()

    def _gone(self):
        self.manager._remove(self)
        self.deleteLater()


class ToastManager(QObject):
    """Owns the live toasts and keeps them stacked above the status bar."""

    def __init__(self, host):
        super().__init__(host)
        self.host = host                 # the window toasts overlay (QMainWindow)
        self.toasts: list[Toast] = []

    def show(self, text: str, kind: str = "info", timeout: int | None = None) -> Toast:
        if timeout is None:
            timeout = _KIND.get(kind, _KIND["info"])[2]
        t = Toast(self, text, kind, timeout)
        self.toasts.append(t)
        t.show()
        self._reflow(new=t)
        return t

    def _remove(self, t: Toast):
        if t in self.toasts:
            self.toasts.remove(t)
            self._reflow()

    def _positions(self):
        # top-centre stack: horizontally centred, just below the toolbar, newest on
        # top (older ones pushed down). The user asked for centre placement, not the
        # bottom-right corner.
        host = self.host
        top = 76
        y = top
        out = []
        for t in self.toasts:                    # newest appended -> sits lowest
            t.adjustSize()
            x = max(12, (host.width() - t.width()) // 2)
            out.append((t, QPoint(x, y)))
            y += t.height() + 8
        return out

    def _reflow(self, new: Toast | None = None):
        for t, pos in self._positions():
            t.raise_()
            if t is new:
                t.play_in(pos)                   # new one slides in
            elif not t._dismissing:
                t.move(pos)                      # others shift up instantly

    def relayout(self):
        self._reflow()
