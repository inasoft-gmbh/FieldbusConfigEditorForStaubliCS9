"""In-window overlay: a dimmed backdrop with a centered card.

Used for About and Safety so they appear inside the main window — no separate OS
window, no taskbar entry. Clicking the dimmed area or pressing Esc closes it.
Cards expose `closeRequested` (and Safety also `changed`).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QMessageBox,
)

from . import theme


# ------------------------------------------------------------------- backdrop
class Overlay(QWidget):
    """Dimmed full-window backdrop hosting one centered card at a time."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("Overlay")
        self.setStyleSheet("#Overlay { background: rgba(8, 10, 14, 165); }")
        self.setFocusPolicy(Qt.StrongFocus)
        self._card: QWidget | None = None
        self.hide()

    def show_card(self, card: QWidget):
        self.close_card()
        self._card = card
        card.setParent(self)
        if hasattr(card, "closeRequested"):
            card.closeRequested.connect(self.close_card)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        card.show()
        self._center()
        self.setFocus()

    def _center(self):
        if self._card is not None:
            self._card.adjustSize()
            x = max(20, (self.width() - self._card.width()) // 2)
            y = max(20, (self.height() - self._card.height()) // 2)
            self._card.move(x, y)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._center()

    def mousePressEvent(self, e):
        # click outside the card -> dismiss
        if self._card is not None and \
                not self._card.geometry().contains(e.position().toPoint()):
            self.close_card()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_card()
        else:
            super().keyPressEvent(e)

    def close_card(self):
        if self._card is not None:
            self._card.deleteLater()
            self._card = None
        self.hide()


def _card_frame(width: int = 460) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("OverlayCard")
    card.setFixedWidth(width)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(26, 24, 26, 20)
    lay.setSpacing(12)
    return card, lay


def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {theme.BORDER};")
    return f


# ---------------------------------------------------------------- About card
class AboutCard(QFrame):
    """About content as an overlay card (logo, partner line, licence, trademarks)."""

    closeRequested = Signal()

    def __init__(self):
        super().__init__()
        import fbconfig as fb
        from fbconfig.paths import asset
        self.setObjectName("OverlayCard")
        self.setFixedWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)

        logo = QLabel()
        pm = QPixmap(asset("inasoft_logo.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaledToHeight(120, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)
        root.addWidget(logo)

        app = QLabel(fb.APP_NAME)
        app.setObjectName("H1")
        app.setAlignment(Qt.AlignCenter)
        root.addWidget(app)
        ver = QLabel(f"Version {fb.__version__}")
        ver.setObjectName("Dim")
        ver.setAlignment(Qt.AlignCenter)
        root.addWidget(ver)

        root.addWidget(_hline())

        info = QLabel(
            f"<p align='center'><b>{fb.COMPANY}</b><br>{fb.TAGLINE}</p>"
            f"<p align='center'><a style='color:{theme.ACCENT};' href='{fb.WEBSITE}'>"
            f"{fb.WEBSITE}</a></p>"
            "<p align='center'>A tool to create and edit Hilscher netX fieldbus "
            "configurations for Stäubli robots (POWERLINK, EtherNet/IP, "
            "EtherCAT/FSoE, PROFINET/PROFIsafe) — without SyCon.net.</p>")
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        root.addWidget(info)

        lic = QLabel(
            f"<p align='center' style='color:{theme.TEXT_DIM};font-size:11px;'>"
            f"© 2026 {fb.COMPANY}. Open source under the GNU GPL v3.<br>"
            "“inasoft” and the inasoft logo are trademarks of inasoft GmbH.<br>"
            "Stäubli is a trademark of Stäubli International AG; this project is "
            "independent and not affiliated with or endorsed by Stäubli.<br>"
            "SyCon.net &amp; netX (Hilscher), POWERLINK (EPSG), EtherCAT &amp; FSoE "
            "(Beckhoff), PROFINET &amp; PROFIsafe (PI), EtherNet/IP (ODVA) are "
            "trademarks of their respective owners.<br>"
            "Provided without any warranty — verify configurations in SyCon.net "
            "before downloading. No liability (see LICENSE, §15–17).</p>")
        lic.setWordWrap(True)
        lic.setTextInteractionFlags(Qt.TextBrowserInteraction)
        root.addWidget(lic)

        bar = QHBoxLayout()
        bar.addStretch()
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.closeRequested.emit)
        bar.addWidget(close)
        root.addLayout(bar)


# ------------------------------------------------------------- Confirm card
class ConfirmCard(QFrame):
    """A centered, dimmed-backdrop confirmation (like About/Safety) instead of a side
    panel — for delete / save prompts. Emits `confirmed` on the action button; the
    overlay closes it either way."""

    closeRequested = Signal()
    confirmed = Signal()

    def __init__(self, title, message, detail=None, confirm_text="Delete", danger=True,
                 cancel=True):
        super().__init__()
        self.setObjectName("OverlayCard")
        self.setFixedWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(12)

        t = QLabel(title)
        t.setObjectName("H1")
        root.addWidget(t)
        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 13px; font-weight: 600;"
                          + (f" color: {theme.ERR};" if danger else ""))
        root.addWidget(msg)
        if detail:
            d = QLabel(detail)
            d.setObjectName("Dim")
            d.setWordWrap(True)
            root.addWidget(d)

        bar = QHBoxLayout()
        bar.addStretch()
        if cancel:
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(self.closeRequested.emit)
            bar.addWidget(cancel_btn)
        ok = QPushButton(confirm_text)
        ok.setObjectName("Primary")
        if danger:
            ok.setStyleSheet(
                f"QPushButton {{ background: {theme.ERR}; color: white; border: 0; "
                "border-radius: 6px; padding: 6px 16px; font-weight: 600; }"
                f"QPushButton:hover {{ background: {theme.ERR}; }}")
        ok.clicked.connect(self.confirmed.emit)
        ok.clicked.connect(self.closeRequested.emit)
        bar.addWidget(ok)
        root.addLayout(bar)
        ok.setFocus()


# ---------------------------------------------------------------- Safety card
class SafetyCard(QFrame):
    """Manage the functional-safety variant (FSoE / PROFIsafe) as an overlay card:
    switch when both variants exist, apply a saved template, or save the current
    variant as a template. Emits `changed` when the active variant changed."""

    closeRequested = Signal()
    changed = Signal()

    def __init__(self, robot_dir, model):
        super().__init__()
        from fbconfig import safety
        self.safety = safety
        self.robot_dir = robot_dir
        self.model = model
        saf = model.raw.get("safety", {})
        self.setObjectName("OverlayCard")
        self.setFixedWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(12)

        t = QLabel(f"{saf.get('tech', 'Safety')}  —  "
                   f"{'ON' if saf.get('safe') else 'OFF'}")
        t.setObjectName("H1")
        root.addWidget(t)

        if saf.get("can_switch"):
            b = QPushButton(f"Switch {saf['tech']} "
                            f"{'OFF' if saf['safe'] else 'ON'}  (other variant is present)")
            b.setObjectName("Primary")
            b.clicked.connect(self._switch)
            root.addWidget(b)
        elif (saf.get("tech") == "PROFIsafe" and not saf.get("safe")):
            # PROFINET: the safe variant is byte-identical to this non-safe one bar
            # the "_safe" name (verified). Generate it in place, keeping every UUID.
            b = QPushButton("Generate safe variant from this config  (keeps UUIDs)")
            b.setObjectName("Primary")
            b.clicked.connect(self._generate)
            root.addWidget(b)
            lbl = QLabel("Creates the PROFIsafe (_safe) variant by renaming — UUIDs, "
                         "configMD5 and the .nxd stay identical. Load the safety "
                         "program separately in SRS.")
            lbl.setObjectName("Dim")
            lbl.setWordWrap(True)
            root.addWidget(lbl)
        else:
            lbl = QLabel("The other variant is not present in this robot. Apply a "
                         "saved template to bring it in, or save this variant as a "
                         "template for other robots.")
            lbl.setObjectName("Dim")
            lbl.setWordWrap(True)
            root.addWidget(lbl)

        # Variant templates are only needed where the safe variant CAN'T be
        # generated (EtherCAT/FSoE: its safe PDOs genuinely differ). For PROFINET the
        # safe variant is a pure rename (Generate above + Switch), so the template
        # apply/save UI is pointless noise -> hide it.
        if saf.get("tech") != "PROFIsafe":
            root.addWidget(_hline())
            templates = self.safety.list_templates()
            row = QHBoxLayout()
            self.tmpl = QComboBox()
            for tp in templates:
                self.tmpl.addItem(f"{tp['name']}  ({tp['kind']} · {tp.get('protocol','')})",
                                  tp["name"])
            row.addWidget(QLabel("Template"))
            row.addWidget(self.tmpl, 1)
            ap = QPushButton("Apply")
            ap.clicked.connect(self._apply)
            ap.setEnabled(bool(templates))
            row.addWidget(ap)
            root.addLayout(row)

            row2 = QHBoxLayout()
            self.name = QLineEdit()
            self.name.setPlaceholderText("template name (e.g. cs9_ethercat_safe)")
            row2.addWidget(QLabel("Save as"))
            row2.addWidget(self.name, 1)
            sv = QPushButton("Save current")
            sv.clicked.connect(self._save)
            row2.addWidget(sv)
            root.addLayout(row2)

        self.msg = QLabel()
        self.msg.setObjectName("Dim")
        self.msg.setWordWrap(True)
        root.addWidget(self.msg)

        bar = QHBoxLayout()
        bar.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.closeRequested.emit)
        bar.addWidget(close)
        root.addLayout(bar)

    def _switch(self):
        try:
            self.safety.switch(self.robot_dir)
        except Exception as e:
            self.msg.setText(f"Switch failed: {e}")
            return
        self.changed.emit()

    def _generate(self):
        # Deliberate button press; a timestamped backup is made first. Result/errors
        # show inline in self.msg.
        try:
            self.safety.generate_safe_variant(self.robot_dir)
        except Exception as e:
            self.msg.setText(f"Generate failed: {e}")
            return
        self.changed.emit()

    def _apply(self):
        name = self.tmpl.currentData()
        if not name:
            return
        # No extra pop-up: applying is a deliberate button press and a timestamped
        # backup is made first. Result/errors show inline in self.msg.
        try:
            self.safety.apply_template(self.robot_dir, name)
        except Exception as e:
            self.msg.setText(f"Apply failed: {e}")
            return
        self.changed.emit()

    def _save(self):
        name = self.name.text().strip()
        if not name:
            self.msg.setText("Enter a template name first.")
            return
        try:
            self.safety.save_template(self.robot_dir, name)
        except Exception as e:
            self.msg.setText(f"Save failed: {e}")
            return
        self.tmpl.addItem(name, name)
        self.msg.setText(f"Saved current variant as template '{name}'.")
