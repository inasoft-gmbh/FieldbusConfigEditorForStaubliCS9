"""Modal dialogs that mirror the console editor's guided prompts.

Each dialog collects the same inputs the CLI asks for and applies the change
through the exact same core API, so both front-ends behave identically.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel,
    QComboBox, QSpinBox, QLineEdit, QRadioButton, QButtonGroup, QPushButton,
    QListWidget, QListWidgetItem, QWidget, QDialogButtonBox, QFrame, QMessageBox,
)

from fbconfig import settings
from fbconfig.datatypes import CATALOG, by_sycon
from fbconfig.model import Signal
from fbconfig.naming import NamingScheme
from . import theme
from .widgets import DownComboBox
QComboBox = DownComboBox       # dropdowns open downward here too


def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {theme.BORDER};")
    return f


def valid_ip(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


# =========================================================== Add data type(s)
class AddDialog(QDialog):
    """Add separate numbered signals or a single array signal to In/Out.

    Replicates cli.edit.do_add: free-byte limits, all 11 types, separate vs
    array, auto-numbering (prefix/start/digits), and persisting the scheme.
    """

    def __init__(self, model, cfg, parent=None, direction=None):
        super().__init__(parent)
        self.model = model
        self.cfg = cfg
        self.summary = ""
        self._pref_dir = direction        # preselect In/Out from the active table
        self.setWindowTitle("Add data type(s)")
        self.setMinimumWidth(440)
        self._build()
        self._recompute()

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Add data type(s)")
        title.setObjectName("H1")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.dir = QComboBox()
        self.dir.addItems(["In", "Out"])
        if self._pref_dir in ("In", "Out"):
            self.dir.setCurrentText(self._pref_dir)
        form.addRow("Direction", self.dir)

        # position
        self.pos = QComboBox()
        self.pos.addItems(["Append at end", "Insert before signal #"])
        form.addRow("Position", self.pos)
        self.idx = QSpinBox()
        self.idx.setEnabled(False)
        form.addRow("Insert before #", self.idx)

        self.dtype = QComboBox()
        self.dtype.addItems(list(CATALOG.keys()))
        self.dtype.setCurrentText(self.cfg.get("last_type", "word"))
        form.addRow("Data type", self.dtype)

        # mode
        mode_row = QHBoxLayout()
        self.mode_sep = QRadioButton("Separate numbered")
        self.mode_arr = QRadioButton("Single array")
        grp = QButtonGroup(self)
        grp.addButton(self.mode_sep, 0)
        grp.addButton(self.mode_arr, 1)
        (self.mode_arr if self.cfg.get("last_mode") == "array"
         else self.mode_sep).setChecked(True)
        mode_row.addWidget(self.mode_sep)
        mode_row.addWidget(self.mode_arr)
        mode_row.addStretch()
        form.addRow("How to add", mode_row)
        root.addLayout(form)

        root.addWidget(_hline())

        # --- separate-numbered fields ---
        self.sep_box = QWidget()
        sf = QFormLayout(self.sep_box)
        sf.setContentsMargins(0, 0, 0, 0)
        sf.setSpacing(10)
        self.count = QSpinBox()
        self.count.setMinimum(1)
        sf.addRow("Count", self.count)
        self.prefix = QLineEdit()
        sf.addRow("Name prefix", self.prefix)
        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(self.cfg["naming"]["start"])
        sf.addRow("Numbering start", self.start)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setValue(self.cfg["naming"]["digits"])
        self.digits.setToolTip("Zero-pad width: 1 -> 0,1   2 -> 00,01")
        sf.addRow("Digits (zero-pad)", self.digits)
        root.addWidget(self.sep_box)

        # --- array fields ---
        self.arr_box = QWidget()
        af = QFormLayout(self.arr_box)
        af.setContentsMargins(0, 0, 0, 0)
        af.setSpacing(10)
        self.arr_len = QSpinBox()
        self.arr_len.setMinimum(1)
        self.arr_len_lbl = QLabel("Array length")
        af.addRow(self.arr_len_lbl, self.arr_len)
        self.arr_name = QLineEdit()
        af.addRow("Signal name", self.arr_name)
        root.addWidget(self.arr_box)

        self.info = QLabel()
        self.info.setObjectName("Mono")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        # buttons
        bb = QDialogButtonBox()
        self.ok = bb.addButton("Add", QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("Primary")
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        # signals
        for w in (self.dir, self.dtype):
            w.currentIndexChanged.connect(self._recompute)
        self.pos.currentIndexChanged.connect(self._recompute)
        self.mode_sep.toggled.connect(self._recompute)

    # -- helpers --
    def _iface(self):
        return self.model.inp if self.dir.currentText() == "In" else self.model.out

    def _recompute(self, *_):
        iface = self._iface()
        free = iface.free_bytes
        dt = CATALOG[self.dtype.currentText()]
        dname = self.dtype.currentText()
        separate = self.mode_sep.isChecked()

        # position
        insert = self.pos.currentIndex() == 1 and bool(iface.signals)
        self.idx.setEnabled(insert)
        self.idx.setRange(0, max(0, len(iface.signals) - 1))

        self.sep_box.setVisible(separate)
        self.arr_box.setVisible(not separate)

        if separate:
            size_each = 1 if dt.key == "bit" else dt.size
            max_n = free // size_each
            self.count.setMaximum(max(1, max_n))
            self.prefix.setText(f"{iface.direction}_{dname}_")
            unit = "8-bit signals" if dt.key == "bit" else f"{dname} signals"
            self.info.setText(
                f"{iface.direction}: {iface.used_bytes}/{iface.max_bytes} used, "
                f"{free} free  →  up to {max_n} {unit} fit "
                f"({size_each} byte/each).")
            self._ok_ok = max_n >= 1
        else:
            if dt.key == "bit":
                self.arr_len_lbl.setText("Array length in BYTES (×8 bits)")
                self.arr_len.setMaximum(max(1, free))
            else:
                self.arr_len_lbl.setText(f"Array length (number of {dname})")
                self.arr_len.setMaximum(max(1, free // dt.size))
            self.arr_name.setText(f"{iface.direction}_{dname}_array")
            self.info.setText(
                f"{iface.direction}: {iface.used_bytes}/{iface.max_bytes} used, "
                f"{free} free.")
            self._ok_ok = free >= (1 if dt.key == "bit" else dt.size)

        if free <= 0:
            self.info.setText(
                f"ERROR: {iface.direction} is full "
                f"({iface.used_bytes}/{iface.max_bytes} bytes). Free space first.")
            self._ok_ok = False
        self.ok.setEnabled(self._ok_ok)

    def _apply(self):
        iface = self._iface()
        dname = self.dtype.currentText()
        dt = CATALOG[dname]
        insert = self.pos.currentIndex() == 1 and bool(iface.signals)
        index = self.idx.value() if insert else len(iface.signals)

        if self.mode_sep.isChecked():
            count = self.count.value()
            prefix = self.prefix.text().strip()
            start, digits = self.start.value(), self.digits.value()
            self.cfg["naming"] = {"start": start, "digits": digits}
            self.cfg["last_type"], self.cfg["last_mode"] = dname, "single"
            settings.save(self.cfg)
            scheme = NamingScheme(prefix, start, digits)
            arr = 8 if dt.key == "bit" else 1
            for i in range(count):
                iface.insert(index + i, Signal(scheme.name(i), dname, array_elements=arr))
            self.summary = (f"Added {count} × {dname} to {iface.direction}. "
                            f"{iface.used_bytes}/{iface.max_bytes} used, "
                            f"{iface.free_bytes} free.")
        else:
            if dt.key == "bit":
                arr = self.arr_len.value() * 8
            else:
                arr = self.arr_len.value()
            name = self.arr_name.text().strip() or f"{iface.direction}_{dname}_array"
            self.cfg["last_type"], self.cfg["last_mode"] = dname, "array"
            settings.save(self.cfg)
            iface.insert(index, Signal(name, dname, array_elements=arr))
            self.summary = (f"Added array '{name}' ({dname} ×{arr}) to "
                            f"{iface.direction}. {iface.free_bytes} free.")
        self.accept()


# ============================================================== Resize / size
class ResizeDialog(QDialog):
    """Change an interface's total byte count (INPUT_LENGTH / OUTPUT_LENGTH)."""

    def __init__(self, model, parent=None, direction=None):
        super().__init__(parent)
        self.model = model
        self.summary = ""
        self.setWindowTitle("Change interface size")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        t = QLabel("Change interface size")
        t.setObjectName("H1")
        root.addWidget(t)

        form = QFormLayout()
        form.setSpacing(10)
        self.dir = QComboBox()
        self.dir.addItems(["In", "Out"])
        if direction in ("In", "Out"):
            self.dir.setCurrentText(direction)
        form.addRow("Direction", self.dir)
        self.size = QSpinBox()
        self.size.setMaximum(1490)
        form.addRow("Total bytes (max)", self.size)
        root.addLayout(form)

        self.info = QLabel()
        self.info.setObjectName("Mono")
        root.addWidget(self.info)
        hint = QLabel("Size change is applied to all 3 files when saving.")
        hint.setObjectName("Dim")
        root.addWidget(hint)

        bb = QDialogButtonBox()
        ok = bb.addButton("Apply", QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self.dir.currentIndexChanged.connect(self._sync)
        self._sync()

    def _iface(self):
        return self.model.inp if self.dir.currentText() == "In" else self.model.out

    def _sync(self):
        iface = self._iface()
        self.size.setMinimum(iface.used_bytes)
        self.size.setValue(iface.max_bytes)
        self.info.setText(f"{iface.direction}: max {iface.max_bytes}, "
                          f"used {iface.used_bytes} (cannot go below used).")

    def _apply(self):
        iface = self._iface()
        new = self.size.value()
        iface.max_bytes = new
        self.summary = (f"{iface.direction} max set to {new} "
                        f"({iface.free_bytes} free).")
        self.accept()


# ============================================================== General data
class GeneralDialog(QDialog):
    """Edit Node ID, card IP and network (DNS) name in one place."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.summary = ""
        d = model.device
        self.setWindowTitle("General data")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        t = QLabel("General data")
        t.setObjectName("H1")
        root.addWidget(t)

        form = QFormLayout()
        form.setSpacing(10)
        self.node = QSpinBox()
        self.node.setRange(1, 239)
        self.node.setValue(d.node_id or 1)
        form.addRow("Node ID (1–239)", self.node)
        self.ip = QLineEdit(d.ip or "")
        self.ip.setPlaceholderText("a.b.c.d")
        form.addRow("Card IP", self.ip)
        self.name = QLineEdit(d.node_name or "")
        form.addRow("Network name", self.name)
        root.addLayout(form)

        self.err = QLabel()
        self.err.setStyleSheet(f"color: {theme.ERR};")
        root.addWidget(self.err)

        bb = QDialogButtonBox()
        ok = bb.addButton("Apply", QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _apply(self):
        ip = self.ip.text().strip()
        if ip and not valid_ip(ip):
            self.err.setText("Invalid IP address (expected a.b.c.d, 0–255).")
            return
        d = self.model.device
        d.node_id = self.node.value()
        d.ip = ip
        d.node_name = self.name.text().strip()
        self.summary = f"Node {d.node_id}, IP {d.ip or '-'}, name {d.node_name or '-'}."
        self.accept()


# ============================================================== EtherNet/IP add
class EipAddDialog(QDialog):
    """Add bit-granular signals to an EtherNet/IP direction. Only data types that
    already exist in the configuration are offered (so a write template exists).
    New signals are inserted and the interface re-packs the bit offsets."""

    BITS = {"bit": 1, "signed8": 8, "unsigned8": 8, "byte": 8, "signed16": 16,
            "word": 16, "signed32": 32, "real32": 32, "unsigned16": 16,
            "unsigned32": 32, "dword": 32}

    def __init__(self, model, cfg, available_types, direction, parent=None):
        super().__init__(parent)
        self.model = model
        self.cfg = cfg
        self.signals = []          # result: built Signal objects
        self.insert_index = None   # result: where to insert
        self._dir = direction or "In"
        self.setWindowTitle("Add signals (EtherNet/IP)")
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        t = QLabel("Add signals (EtherNet/IP)")
        t.setObjectName("H1")
        root.addWidget(t)

        form = QFormLayout()
        form.setSpacing(10)
        self.dir = QComboBox()
        self.dir.addItems(["In", "Out"])
        self.dir.setCurrentText(self._dir)
        form.addRow("Direction", self.dir)
        self.pos = QComboBox()
        self.pos.addItems(["Append at end", "Insert before signal #"])
        form.addRow("Position", self.pos)
        self.idx = QSpinBox()
        self.idx.setEnabled(False)
        form.addRow("Insert before #", self.idx)
        self.dtype = QComboBox()
        self.dtype.addItems(available_types)
        form.addRow("Data type", self.dtype)
        self.count = QSpinBox()
        self.count.setMinimum(1)
        form.addRow("Count", self.count)
        self.prefix = QLineEdit()
        form.addRow("Name prefix", self.prefix)
        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(cfg["naming"]["start"])
        form.addRow("Numbering start", self.start)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setValue(cfg["naming"]["digits"])
        form.addRow("Digits (zero-pad)", self.digits)
        root.addLayout(form)

        self.info = QLabel()
        self.info.setObjectName("Mono")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        bb = QDialogButtonBox()
        self.ok = bb.addButton("Add", QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("Primary")
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        for w in (self.dir, self.dtype):
            w.currentIndexChanged.connect(self._recompute)
        self.pos.currentIndexChanged.connect(self._recompute)
        self.count.valueChanged.connect(self._update_info)
        self._recompute()

    def _iface(self):
        return self.model.inp if self.dir.currentText() == "In" else self.model.out

    def _free_bits(self):
        iface = self._iface()
        return iface.max_bytes * 8 - iface.used_bits

    def _recompute(self, *_):
        iface = self._iface()
        insert = self.pos.currentIndex() == 1 and bool(iface.signals)
        self.idx.setEnabled(insert)
        self.idx.setRange(0, max(0, len(iface.signals) - 1))
        dn = self.dtype.currentText()
        self.prefix.setText(f"{iface.direction}_{dn}_")
        per = self.BITS.get(dn, 8)
        self.count.setMaximum(max(1, self._free_bits() // per))
        self._update_info()

    def _update_info(self, *_):
        dn = self.dtype.currentText()
        per = self.BITS.get(dn, 8)
        free = self._free_bits()
        self.info.setText(
            f"{self._iface().direction}: {free} free bit(s) ({free // 8} byte). "
            f"One {dn} = {per} bit. Multi-byte types are byte-aligned, so actual "
            f"capacity may be a little lower (checked on add).")
        self.ok.setEnabled(free >= per)

    def _apply(self):
        from fbconfig.model import Signal
        from fbconfig.naming import NamingScheme
        iface = self._iface()
        dn = self.dtype.currentText()
        st = "input" if iface.direction == "In" else "output"
        insert = self.pos.currentIndex() == 1 and bool(iface.signals)
        index = self.idx.value() if insert else len(iface.signals)
        start, digits = self.start.value(), self.digits.value()
        self.cfg["naming"] = {"start": start, "digits": digits}
        self.cfg["last_type"] = dn
        settings.save(self.cfg)
        scheme = NamingScheme(self.prefix.text().strip(), start, digits)
        self.signals = [Signal(scheme.name(i), dn, array_elements=1, signal_type=st)
                        for i in range(self.count.value())]
        self.insert_index = index
        self.direction = iface.direction
        self.accept()


# ============================================================== Batch rename
class BatchRenameDialog(QDialog):
    """Rename several selected signals at once: prefix + auto-numbering.

    Same naming scheme as 'Add' (prefix / start / digits), applied to the
    selected signals in their current order.
    """

    def __init__(self, count, cfg, direction, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Rename signals")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        t = QLabel(f"Rename {count} signals")
        t.setObjectName("H1")
        root.addWidget(t)

        form = QFormLayout()
        form.setSpacing(10)
        self.prefix = QLineEdit(f"{direction}_")
        form.addRow("Name prefix", self.prefix)
        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(cfg["naming"]["start"])
        form.addRow("Numbering start", self.start)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setValue(cfg["naming"]["digits"])
        self.digits.setToolTip("Zero-pad width: 1 -> 0,1   2 -> 00,01")
        form.addRow("Digits (zero-pad)", self.digits)
        root.addLayout(form)

        self.preview = QLabel()
        self.preview.setObjectName("Mono")
        root.addWidget(self.preview)

        bb = QDialogButtonBox()
        ok = bb.addButton("Rename", QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        for w in (self.prefix, self.start, self.digits):
            (w.textChanged if isinstance(w, QLineEdit)
             else w.valueChanged).connect(self._upd)
        self._upd()

    def scheme(self) -> NamingScheme:
        return NamingScheme(self.prefix.text().strip(),
                            self.start.value(), self.digits.value())

    def _upd(self, *_):
        s = self.scheme()
        names = [s.name(0), s.name(1), s.name(2)]
        self.preview.setText("e.g.  " + ", ".join(names) + ", ...")

    def accept(self):
        self.cfg["naming"] = {"start": self.start.value(), "digits": self.digits.value()}
        settings.save(self.cfg)
        super().accept()


# ===================================================== General (modular protos)
class StationDialog(QDialog):
    """Edit the network identity of a bit-granular protocol (EtherNet/IP IP,
    EtherCAT station, PROFINET device name) — stored in the Val3 stationAddress.
    The SyCon project (SYCON_net.xml) keeps its value; re-validate in SyCon."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.summary = ""
        self.fields = dict(model.raw.get("station_fields") or {"kind": "raw", "raw": ""})
        self.setWindowTitle("General data")
        self.setMinimumWidth(400)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        t = QLabel("General data")
        t.setObjectName("H1")
        root.addWidget(t)
        sub = QLabel(f"{model.device.protocol} network identity (Val3 export).")
        sub.setObjectName("Dim")
        root.addWidget(sub)

        form = QFormLayout()
        form.setSpacing(10)
        kind = self.fields.get("kind")
        if kind == "ip":
            self.edit = QLineEdit(self.fields.get("ip", ""))
            self.edit.setPlaceholderText("a.b.c.d")
            form.addRow("IP address", self.edit)
        elif kind == "name":
            self.edit = QLineEdit(self.fields.get("name", ""))
            self.edit.setPlaceholderText("device / station name")
            form.addRow("Device name", self.edit)
        elif kind == "station":
            self.edit = QLineEdit(self.fields.get("station", ""))
            self.edit.setPlaceholderText("station address / alias")
            form.addRow("Station address", self.edit)
        else:
            self.edit = QLineEdit(self.fields.get("raw", ""))
            form.addRow("Station address", self.edit)
        root.addLayout(form)

        self.err = QLabel()
        self.err.setStyleSheet(f"color: {theme.ERR};")
        root.addWidget(self.err)
        note = QLabel("Written to the Val3 export and the SyCon project (IP / name "
                      "patched in place). Re-validate in SyCon.net before download.")
        note.setObjectName("Dim")
        note.setWordWrap(True)
        root.addWidget(note)

        bb = QDialogButtonBox()
        ok = bb.addButton("Apply", QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _apply(self):
        from fbconfig.protocols import ethernetip as eip
        val = self.edit.text().strip()
        kind = self.fields.get("kind")
        if kind == "ip" and not valid_ip(val):
            self.err.setText("Invalid IP address (expected a.b.c.d, 0–255).")
            return
        if not val:
            self.err.setText("Value must not be empty.")
            return
        self.fields[{"ip": "ip", "name": "name", "station": "station"}.get(kind, "raw")] = val
        new_station = eip.build_station(self.fields)
        self.model.raw["station_new"] = new_station
        self.model.raw["station_fields"] = self.fields
        d = self.model.device
        if kind == "ip":
            d.ip = val
        elif kind == "name":
            d.node_name = val
        elif kind == "station":
            try:
                d.node_id = int(val)
            except ValueError:
                d.node_name = val
        self.summary = f"Network identity set to '{new_station}'."
        self.accept()


# ============================================================== Safety dialog
class SafetyDialog(QDialog):
    """Manage the functional-safety variant (FSoE / PROFIsafe): switch when both
    variants exist, save the current variant as a reusable template, or apply a
    saved template to bring in the other variant (e.g. turn FSoE off on a robot
    that only has the safe config). The robot-specific IP/station is set via
    General after switching."""

    def __init__(self, robot_dir, model, parent=None):
        super().__init__(parent)
        from fbconfig import safety
        self.safety = safety
        self.robot_dir = robot_dir
        self.model = model
        self.changed = False
        saf = model.raw.get("safety", {})
        self.setWindowTitle("Safety")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        t = QLabel(f"{saf.get('tech', 'Safety')}  —  "
                   f"{'ON' if saf.get('safe') else 'OFF'}")
        t.setObjectName("H1")
        root.addWidget(t)

        # 1) direct switch (both variants present)
        if saf.get("can_switch"):
            b = QPushButton(f"Switch {saf['tech']} "
                            f"{'OFF' if saf['safe'] else 'ON'}  (other variant is present)")
            b.setObjectName("Primary")
            b.clicked.connect(self._switch)
            root.addWidget(b)
        else:
            lbl = QLabel("The other variant is not present in this robot. Apply a "
                         "saved template to bring it in, or save this variant as a "
                         "template for other robots.")
            lbl.setObjectName("Dim")
            lbl.setWordWrap(True)
            root.addWidget(lbl)

        root.addWidget(_hline())

        # 2) apply a saved template
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

        # 3) save current variant as a template
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

        bb = QDialogButtonBox()
        bb.addButton("Close", QDialogButtonBox.RejectRole)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _switch(self):
        try:
            self.safety.switch(self.robot_dir)
        except Exception as e:
            self.msg.setText(f"Switch failed: {e}")
            return
        self.changed = True
        self.accept()

    def _apply(self):
        name = self.tmpl.currentData()
        if not name:
            return
        # No extra pop-up: deliberate button + a timestamped backup is made first.
        try:
            self.safety.apply_template(self.robot_dir, name)
        except Exception as e:
            self.msg.setText(f"Apply failed: {e}")
            return
        self.changed = True
        self.accept()

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


# ============================================================== About
class AboutDialog(QDialog):
    """About box — shows the inasoft logo, the company as Stäubli programming
    partner, a website link, the licence and the warranty disclaimer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        import fbconfig as fb
        from fbconfig.paths import asset
        from PySide6.QtGui import QPixmap
        self.setWindowTitle("About")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
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

        bb = QDialogButtonBox()
        ok = bb.addButton("Close", QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        bb.accepted.connect(self.accept)
        root.addWidget(bb)


# ============================================================== Project picker
class ProjectPicker(QDialog):
    """Choose among several fieldbus projects found under a robot folder."""

    def __init__(self, projects, parent=None):
        super().__init__(parent)
        self.projects = projects
        self.selected = 0
        self.setWindowTitle("Select project")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        t = QLabel(f"Found {len(projects)} fieldbus projects")
        t.setObjectName("H1")
        root.addWidget(t)
        self.list = QListWidget()
        for p in projects:
            QListWidgetItem(f"{p.spj.parent.name} / {p.base_name}", self.list)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept())
        root.addWidget(self.list)

        bb = QDialogButtonBox()
        ok = bb.addButton("Open", QDialogButtonBox.AcceptRole)
        ok.setObjectName("Primary")
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _accept(self):
        self.selected = self.list.currentRow()
        self.accept()


# ============================================================== New config
class NewConfigDialog(QDialog):
    """Pick a fieldbus protocol; a minimal reference configuration of that protocol
    is created (cloned from a bundled template). The user then edits signals/IP."""

    PROTOCOLS = [
        ("powerlink", "POWERLINK", "Ethernet POWERLINK · Hilscher CIFX RE/PLS"),
        ("ethernetip", "EtherNet/IP", "EtherNet/IP adapter · netX RE/EIS"),
        ("ethercat", "EtherCAT", "EtherCAT slave · CPT RE/ECS (FSoE-capable)"),
        ("profinet", "PROFINET", "PROFINET device · netX RE/PNS (PROFIsafe-capable)"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.protocol_key = None
        self.protocol_name = None
        self.setWindowTitle("New configuration")
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        t = QLabel("Choose a fieldbus protocol")
        t.setObjectName("H1")
        root.addWidget(t)
        sub = QLabel("A minimal reference configuration is created automatically. "
                     "Then edit the signals, network name and IP, and save.")
        sub.setObjectName("Dim")
        sub.setWordWrap(True)
        root.addWidget(sub)

        for key, name, desc in self.PROTOCOLS:
            b = QPushButton()
            b.setCursor(Qt.PointingHandCursor)
            b.setText(f"{name}\n{desc}")
            b.setStyleSheet(
                f"QPushButton {{ text-align: left; padding: 11px 14px; border: 1px "
                f"solid {theme.BORDER}; border-radius: 8px; background: {theme.PANEL_HI};"
                f" font-weight: 600; }}"
                f" QPushButton:hover {{ border-color: {theme.ACCENT}; }}")
            b.clicked.connect(lambda _=False, k=key, n=name: self._pick(k, n))
            root.addWidget(b)

        bb = QDialogButtonBox()
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _pick(self, key, name):
        self.protocol_key = key
        self.protocol_name = name
        self.accept()
