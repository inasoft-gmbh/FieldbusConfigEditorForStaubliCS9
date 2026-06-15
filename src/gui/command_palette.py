"""Command palette (Ctrl+P): type to run any action — the modern 'prompt' way.

A frameless popup with a search box over a ranked list of the toolbar actions.
Respects each action's enabled/visible state, so it only offers what is valid
for the current project. Triggering an item simply calls the QAction.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLineEdit, QListWidget,
                              QListWidgetItem, QLabel, QHBoxLayout, QWidget)

from . import theme


def _score(query: str, label: str):
    """Rank a label against the query. Returns None if it doesn't match.

    Lower is better: prefix < word-start < substring < subsequence.
    """
    q, l = query.lower().strip(), label.lower()
    if not q:
        return 0
    if l.startswith(q):
        return 0
    pos = l.find(q)
    if pos != -1:
        return 1 if l[pos - 1] == " " else 2
    # subsequence fallback (typed letters appear in order)
    i = 0
    for ch in l:
        if i < len(q) and ch == q[i]:
            i += 1
    return 3 if i == len(q) else None


class CommandPalette(QDialog):
    def __init__(self, commands, parent):
        # commands: list of (label, shortcut, QAction)
        super().__init__(parent, Qt.Popup)
        self.commands = commands
        self.setObjectName("Palette")
        self.setFixedWidth(540)
        self.setStyleSheet(
            f"#Palette {{ background: {theme.PANEL}; border: 1px solid {theme.ACCENT};"
            f" border-radius: 12px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Type a command…   (↑/↓ to choose · Enter to run · Esc to close)")
        self.edit.setClearButtonEnabled(True)
        f = self.edit.font()
        f.setPointSize(f.pointSize() + 2)
        self.edit.setFont(f)
        lay.addWidget(self.edit)

        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setStyleSheet(
            f"QListWidget {{ background: {theme.BG}; border: 1px solid {theme.BORDER};"
            f" border-radius: 8px; outline: 0; }}"
            f" QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f" QListWidget::item:selected {{ background: {theme.ACCENT_DIM};"
            f" color: {theme.TEXT}; }}")
        lay.addWidget(self.list)

        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        self.list.itemActivated.connect(lambda *_: self._run_current())
        self.list.itemClicked.connect(lambda *_: self._run_current())
        self.edit.installEventFilter(self)        # route ↑/↓ into the list

        self._filter("")

    # ---- behaviour ----
    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                n = self.list.count()
                if n:
                    row = self.list.currentRow()
                    row = (row + (1 if k == Qt.Key_Down else -1)) % n
                    self.list.setCurrentRow(row)
                return True
            if k == Qt.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, ev)

    def _filter(self, text):
        self.list.clear()
        scored = []
        for label, shortcut, action in self.commands:
            s = _score(text, label)
            if s is not None:
                scored.append((s, label, shortcut, action))
        scored.sort(key=lambda t: (t[0], t[1].lower()))
        for _, label, shortcut, action in scored:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, action)
            if shortcut:
                it.setData(Qt.UserRole + 1, shortcut)
                row = self._row_widget(label, shortcut)
                it.setSizeHint(row.sizeHint())
                self.list.addItem(it)
                self.list.setItemWidget(it, row)
            else:
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _row_widget(self, label, shortcut):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        name = QLabel(label)
        h.addWidget(name)
        h.addStretch()
        key = QLabel(shortcut)
        key.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-family: {theme.MONO};"
            f" border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 1px 6px;")
        h.addWidget(key)
        return w

    def _run_current(self):
        it = self.list.currentItem()
        if it is None:
            return
        action = it.data(Qt.UserRole)
        self.close()
        if action is not None and action.isEnabled():
            action.trigger()
