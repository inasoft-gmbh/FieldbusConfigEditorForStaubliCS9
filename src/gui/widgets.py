"""Small shared widgets."""
from __future__ import annotations
from PySide6.QtWidgets import QComboBox


class DownComboBox(QComboBox):
    """A combo box whose dropdown ALWAYS opens downward — Qt otherwise flips the
    popup upward when the box is near the bottom of the screen / a docked panel,
    which hid the options. We re-place the popup just below the box after it shows."""

    def showPopup(self):
        super().showPopup()
        popup = self.view().window()
        below = self.mapToGlobal(self.rect().bottomLeft())
        popup.move(below.x(), below.y())
