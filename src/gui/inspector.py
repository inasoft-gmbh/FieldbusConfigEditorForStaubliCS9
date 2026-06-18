"""Docked, non-modal inspector panel that hosts the editing forms inline.

Add / Resize / General (and the EtherNet/IP variants) used to be modal pop-up
windows. Here their bodies live as plain widgets inside a side panel that docks
on the right of the main window, so the signal tables stay visible while editing.

The form logic is taken verbatim from the former dialogs (dialogs.py) so the
byte-exact behaviour is unchanged — only the framing (panel vs. window) differs.
About / Safety / project picker / batch rename stay as small dialogs.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QSpinBox, QLineEdit, QRadioButton, QButtonGroup, QPushButton, QScrollArea,
    QListWidget, QCheckBox,
)


from .widgets import DownComboBox
QComboBox = DownComboBox       # all combos in this module's forms open downward

from fbconfig import settings  # noqa: E402
from fbconfig.datatypes import CATALOG, by_sycon, bit_width
from fbconfig.model import Signal as Sig
from fbconfig.naming import NamingScheme
from . import theme
from .dialogs import valid_ip, _hline


def en_label(s: str) -> str:
    """Display-only translation of the German PROFINET module/direction words to English.
    The device-XML moduleType keeps the GSDML German names (SyCon matches on them), so
    this is used ONLY for what the user sees, never for what is written."""
    return (s.replace("Eingänge", "Inputs").replace("Ausgänge", "Outputs")
             .replace("Eingang", "Input").replace("Ausgang", "Output"))


class InspectorForm(QWidget):
    """Base for a panel form. apply() mutates the model and returns a summary
    string, or raises ValueError with a message the panel shows. emit `changed`
    whenever validity may have changed so the panel can enable/disable Apply."""

    changed = Signal()

    # set by add-style forms so the window can flash the new rows
    added = None
    direction = None

    def is_valid(self) -> bool:
        return True

    def apply(self) -> str:                      # pragma: no cover - overridden
        raise NotImplementedError


# ----------------------------------------------------------------- Add (bytes)
class AddForm(InspectorForm):
    """Add signals at a chosen START BYTE (byte-granular protocols). The target
    range is highlighted live in the table; if it overlaps existing data or runs
    past the interface size it turns red and Apply is blocked — the user then moves
    a signal (drag) or resizes. Separate numbered signals (count) or one array."""

    # (direction, start_byte, n_bytes, fits) — live 'where the new data lands'
    preview = Signal(str, int, int, bool)

    def __init__(self, model, cfg, direction=None, start_byte=None):
        super().__init__()
        self.model = model
        self.cfg = cfg
        self._pref_dir = direction
        self._pref_start = start_byte
        self._ok_ok = False
        self._build()
        self._init_start()
        self._recompute()

    def _init_start(self):
        iface = self._iface()
        self.startb.setMaximum(max(0, iface.max_bytes))
        # default: the selected row's byte, else the first free byte (append)
        default = self._pref_start if self._pref_start is not None else iface.used_bytes
        self.startb.setValue(min(default, iface.max_bytes))

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        self.dir = QComboBox()
        self.dir.addItems(["In", "Out"])
        if self._pref_dir in ("In", "Out"):
            self.dir.setCurrentText(self._pref_dir)
        form.addRow("Direction", self.dir)
        # The user always picks WHERE: the start byte. Data is laid out from there
        # downward. To append, point it at the first free byte (the default).
        self.startb = QSpinBox()
        self.startb.setToolTip("First byte the new data occupies. Click a row in the "
                               "table to start there; the range is highlighted below.")
        form.addRow("Start byte", self.startb)
        self.dtype = QComboBox()
        self.dtype.addItems(list(CATALOG.keys()))
        self.dtype.setCurrentText(self.cfg.get("last_type", "word"))
        form.addRow("Data type", self.dtype)

        mode_row = QHBoxLayout()
        self.mode_sep = QRadioButton("Separate")
        self.mode_arr = QRadioButton("Array")
        grp = QButtonGroup(self)
        grp.addButton(self.mode_sep, 0)
        grp.addButton(self.mode_arr, 1)
        (self.mode_arr if self.cfg.get("last_mode") == "array"
         else self.mode_sep).setChecked(True)
        mode_row.addWidget(self.mode_sep)
        mode_row.addWidget(self.mode_arr)
        mode_row.addStretch()
        form.addRow("How to add", mode_row)

        # 'bit' granularity: a whole byte of 8 flags (default) or single sub-byte
        # bits (arrayElements=1, address byte.bit). Single bits are packed and
        # appended at the end, so they ignore the start byte (noted live).
        self.bit_gran = QComboBox()
        self.bit_gran.addItems(["Byte (8 flags)", "Single bit"])
        self._form = form
        self.bit_gran_row = form.rowCount()
        form.addRow("Bit granularity", self.bit_gran)
        root.addLayout(form)
        root.addWidget(_hline())

        self.sep_box = QWidget()
        sf = QFormLayout(self.sep_box)
        sf.setContentsMargins(0, 0, 0, 0)
        sf.setSpacing(10)
        self.count = QSpinBox()
        self.count.setMinimum(1)
        self.count.setMaximum(99999)
        sf.addRow("Count", self.count)
        self.prefix = QLineEdit()
        self._prefix_touched = False
        self.prefix.textEdited.connect(lambda *_: setattr(self, "_prefix_touched", True))
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

        self.arr_box = QWidget()
        af = QFormLayout(self.arr_box)
        af.setContentsMargins(0, 0, 0, 0)
        af.setSpacing(10)
        self.arr_len = QSpinBox()
        self.arr_len.setMinimum(1)
        self.arr_len.setMaximum(99999)
        self.arr_len_lbl = QLabel("Array length")
        af.addRow(self.arr_len_lbl, self.arr_len)
        self.arr_name = QLineEdit()
        self._arr_name_touched = False
        self.arr_name.textEdited.connect(lambda *_: setattr(self, "_arr_name_touched", True))
        af.addRow("Signal name", self.arr_name)
        root.addWidget(self.arr_box)

        self.info = QLabel()
        self.info.setObjectName("Mono")
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color: {theme.ERR}; font-weight: 600;")
        self.warn.hide()
        root.addWidget(self.warn)
        root.addStretch()

        for w in (self.dir, self.dtype, self.bit_gran):
            w.currentIndexChanged.connect(self._on_dir_or_type)
        self.mode_sep.toggled.connect(self._recompute)
        for sb in (self.startb, self.count, self.arr_len):
            sb.valueChanged.connect(self._recompute)

    def _on_dir_or_type(self, *_):
        # switching direction re-bases the start byte against the other interface
        self._init_start()
        self._recompute()

    def _iface(self):
        return self.model.inp if self.dir.currentText() == "In" else self.model.out

    def _single_bit(self):
        return (CATALOG[self.dtype.currentText()].key == "bit"
                and self.bit_gran.currentText() == "Single bit")

    def _nbytes(self):
        """Byte footprint of the data being added."""
        dt = CATALOG[self.dtype.currentText()]
        if self._single_bit():
            return (self.count.value() + 7) // 8
        each = 1 if dt.key == "bit" else dt.size
        n = self.count.value() if self.mode_sep.isChecked() else self.arr_len.value()
        return n * each

    def _recompute(self, *_):
        iface = self._iface()
        dt = CATALOG[self.dtype.currentText()]
        dname = self.dtype.currentText()
        separate = self.mode_sep.isChecked()
        try:
            self._form.setRowVisible(self.bit_gran_row, dt.key == "bit")
        except Exception:
            pass
        single_bit = self._single_bit()

        # single bits are packed & appended -> start byte does not apply to them
        self.startb.setEnabled(not single_bit)
        self.sep_box.setVisible(separate or single_bit)
        self.arr_box.setVisible(not separate and not single_bit)
        # keep the auto-suggested name in sync with the data type — until the user edits it
        if separate or single_bit:
            if not self._prefix_touched:
                base = 'bit' if single_bit else _type_label(dname)
                self.prefix.setText(f"{iface.direction}_{base}_")
        else:
            if dt.key == "bit":
                self.arr_len_lbl.setText("Array length in BYTES (×8 bits)")
            else:
                self.arr_len_lbl.setText(f"Array length (number of {dname})")
            if not self._arr_name_touched:
                self.arr_name.setText(f"{iface.direction}_{_type_label(dname)}_array")

        nbytes = self._nbytes()
        if single_bit:
            from fbconfig.datatypes import bit_width
            used_bits = sum(bit_width(s.sycon_dtype, s.array_elements)
                            for s in iface.signals)
            free_bits = iface.max_bytes * 8 - used_bits
            self._ok_ok = self.count.value() <= free_bits
            self.info.setText(
                f"{iface.direction}: {free_bits} free bit(s). Single bits are packed "
                "and appended at the end (address byte.bit).")
        else:
            start = self.startb.value()
            fits = iface.free_run_containing(start, nbytes) is not None
            self._ok_ok = fits and nbytes > 0
            self.info.setText(
                f"{iface.direction}: {iface.used_bytes}/{iface.max_bytes} used. "
                f"New data occupies bytes {start}–{start + nbytes - 1} "
                f"({nbytes} byte).")
        self._update_preview()
        self.changed.emit()

    def _update_preview(self):
        iface = self._iface()
        nbytes = self._nbytes()
        if self._single_bit():
            self.preview.emit(iface.direction, iface.used_bytes, nbytes, True)
            self.warn.hide()
            return
        start = self.startb.value()
        self.preview.emit(iface.direction, start, nbytes, self._ok_ok)
        if not self._ok_ok:
            if start + nbytes > iface.max_bytes:
                self.warn.setText(
                    f"✕ Runs past the interface ({start + nbytes} > "
                    f"{iface.max_bytes} bytes). Resize the interface or lower the "
                    "count.")
            else:
                self.warn.setText(
                    f"✕ Overlaps existing data at byte {start}. Pick a free start "
                    "byte, move a signal (drag), or resize.")
            self.warn.show()
        else:
            self.warn.hide()

    def is_valid(self):
        return self._ok_ok

    def apply(self):
        iface = self._iface()
        dname = self.dtype.currentText()
        dt = CATALOG[dname]
        self.direction = iface.direction
        self.added = []

        if self._single_bit():
            return self._apply_single_bits(iface)

        start = self.startb.value()
        if self.mode_sep.isChecked():
            count = self.count.value()
            prefix = self.prefix.text().strip()
            nstart, digits = self.start.value(), self.digits.value()
            self.cfg["naming"] = {"start": nstart, "digits": digits}
            self.cfg["last_type"], self.cfg["last_mode"] = dname, "single"
            settings.save(self.cfg)
            scheme = NamingScheme(prefix, nstart, digits)
            arr = 8 if dt.key == "bit" else 1
            new = [Sig(scheme.name(i), dname, array_elements=arr) for i in range(count)]
            iface.place_new_at(new, start)            # raises on overlap/overflow
            self.added = new
            return (f"Added {count} × {dname} at byte {start} ({iface.direction}). "
                    f"{iface.used_bytes}/{iface.max_bytes} used.")
        arr = self.arr_len.value() * 8 if dt.key == "bit" else self.arr_len.value()
        name = self.arr_name.text().strip() or f"{iface.direction}_{_type_label(dname)}_array"
        self.cfg["last_type"], self.cfg["last_mode"] = dname, "array"
        settings.save(self.cfg)
        s = Sig(name, dname, array_elements=arr)
        iface.place_new_at([s], start)
        self.added = [s]
        return (f"Added array '{name}' ({dname} ×{arr}) at byte {start} "
                f"({iface.direction}). {iface.free_bytes} free.")

    def _apply_single_bits(self, iface):
        """Append single sub-byte bits (arrayElements=1), packed into bytes. Each
        new bit's pad_before = its byte minus the previous byte, so consecutive
        bits share a byte (pad 0) and the first bit of every new byte advances by 1
        — continuing a partially-filled last byte. Capacity is checked in BITS."""
        from fbconfig.datatypes import bit_width
        count = self.count.value()
        nstart, digits = self.start.value(), self.digits.value()
        self.cfg["naming"] = {"start": nstart, "digits": digits}
        self.cfg["last_type"], self.cfg["last_mode"] = "bit", "single"
        settings.save(self.cfg)
        used_bits = sum(bit_width(s.sycon_dtype, s.array_elements)
                        for s in iface.signals)
        free_bits = iface.max_bytes * 8 - used_bits
        if count > free_bits:
            raise ValueError(
                f"{iface.direction}: only {free_bits} free bit(s) — reduce the "
                "count or resize the interface first.")
        prev_byte = sum(s.pad_before + s.size for s in iface.signals)  # byte cursor
        scheme = NamingScheme(self.prefix.text().strip(), nstart, digits)
        for i in range(count):
            byte = (used_bits + i) // 8
            s = Sig(scheme.name(i), "bit", array_elements=1)
            s.pad_before = byte - prev_byte
            iface.signals.append(s)
            self.added.append(s)
            prev_byte = byte
        return (f"Added {count} single bit(s) to {iface.direction}. "
                "Re-validate in SyCon.net before download.")


# -------------------------------------------------------------------- Resize
class ResizeForm(InspectorForm):
    """Change both interfaces' total byte count (INPUT_LENGTH / OUTPUT_LENGTH) at
    once — In and Out are shown and edited side by side."""

    def __init__(self, model, direction=None):
        super().__init__()
        self.model = model
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)

        def _spin(iface):
            sb = QSpinBox()
            sb.setMaximum(1486)
            # The size is a budget; signals stay put. Can't go below the bytes in use
            # (delete signals first to shrink further).
            sb.setMinimum(iface.used_bytes)
            sb.setValue(iface.max_bytes)
            sb.valueChanged.connect(self._sync)
            return sb

        self.in_size = _spin(model.inp)
        self.out_size = _spin(model.out)
        form.addRow("In  total bytes (max)", self.in_size)
        form.addRow("Out total bytes (max)", self.out_size)
        root.addLayout(form)

        self.info = QLabel()
        self.info.setObjectName("Mono")
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        hint = QLabel("Size change is applied to all 3 files when saving. "
                      "Neither can go below the bytes already used.")
        hint.setObjectName("Dim")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch()

        # focus the direction the user came from, if any
        (self.out_size if direction == "Out" else self.in_size).setFocus()
        self._sync()

    def _sync(self):
        i, o = self.model.inp, self.model.out
        self.info.setText(
            f"In : max {self.in_size.value():>4}  used {i.used_bytes:>4}  "
            f"free {self.in_size.value() - i.used_bytes:>4}\n"
            f"Out: max {self.out_size.value():>4}  used {o.used_bytes:>4}  "
            f"free {self.out_size.value() - o.used_bytes:>4}")

    def apply(self):
        # Size is the configured interface budget; signals are untouched. Growing it adds
        # FREE bytes the user can fill via Add; shrinking frees trailing budget. (EtherCAT
        # writes this size as the SM length; re-validate in SyCon.net.)
        self.model.inp.max_bytes = self.in_size.value()
        self.model.out.max_bytes = self.out_size.value()
        if self.model.raw.get("protocol_kind") == "ethercat" or self.model.raw.get("eip_eis"):
            self.model.raw["layout_dirty"] = True
        return (f"In max {self.model.inp.max_bytes} "
                f"({self.model.inp.free_bytes} free), "
                f"Out max {self.model.out.max_bytes} "
                f"({self.model.out.free_bytes} free).")


# ------------------------------------------------------------ Confirm (inline)
class ConfirmForm(InspectorForm):
    """An inline confirmation hosted in the panel instead of a modal pop-up: a
    message (+ optional detail) and the panel's Apply button runs `on_confirm`,
    whose return value becomes the success summary."""

    def __init__(self, message, on_confirm, detail=None, danger=False):
        super().__init__()
        self.on_confirm = on_confirm
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
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
        root.addStretch()

    def apply(self):
        return self.on_confirm() or ""


# ------------------------------------------------------------ General (bytes)
class GeneralForm(InspectorForm):
    """Edit Node ID, card IP and network (DNS) name (POWERLINK / .nxd)."""

    def __init__(self, model):
        super().__init__()
        self.model = model
        d = model.device
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
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
        root.addStretch()

    def apply(self):
        ip = self.ip.text().strip()
        if ip and not valid_ip(ip):
            raise ValueError("Invalid IP address (expected a.b.c.d, 0–255).")
        d = self.model.device
        d.node_id = self.node.value()
        d.ip = ip
        d.node_name = self.name.text().strip()
        return f"Node {d.node_id}, IP {d.ip or '-'}, name {d.node_name or '-'}."


# ----------------------------------------------- General (modular: station id)
class StationForm(InspectorForm):
    """Edit the network identity of a bit-granular protocol (EtherNet/IP IP,
    EtherCAT station, PROFINET device name) — stored in the Val3 stationAddress."""

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.fields = dict(model.raw.get("station_fields") or {"kind": "raw", "raw": ""})
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        sub = QLabel(f"{model.device.protocol} network identity (Val3 export).")
        sub.setObjectName("Dim")
        sub.setWordWrap(True)
        root.addWidget(sub)
        form = QFormLayout()
        form.setSpacing(10)
        kind = self.fields.get("kind")
        # EtherCAT carries a Val3 stationAddress (the <0> in the device name) that SyCon
        # keeps fixed at 0 and never exposes — EtherCAT addresses by bus position / the
        # Station Alias (shown below). So we don't offer it as an editable field; the
        # settable address for EtherCAT is the Station alias in the General block.
        self.edit = None
        if model.raw.get("protocol_kind") == "ethercat":
            pass
        elif kind == "ip":
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

        # PROFINET: also SHOW the device settings (read-only for now — only the name
        # is written this round; watchdog/startup/byte-order are written once each is
        # verified against a clean single-field SyCon diff, to avoid guessing).
        self._pn = (model.raw.get("protocol_kind") == "profinet")
        self.wd = None
        self.endian = None
        if self._pn:
            wd = model.raw.get("pn_watchdog")
            su = model.raw.get("pn_startup")
            big = model.raw.get("pn_endian_big", True)
            self.wd = QSpinBox()                       # watchdog is editable
            self.wd.setRange(0, 65535)
            self.wd.setValue(int(wd) if wd is not None else 0)
            self.endian = QComboBox()                  # byte order is editable
            self.endian.addItems(["Big Endian (MSB first)", "Little Endian (LSB first)"])
            self.endian.setCurrentIndex(0 if big else 1)
            self.startup = QComboBox()                  # bus startup is editable
            self.startup.addItems(["Automatically by device",
                                   "Controlled by application"])
            self.startup.setCurrentIndex(1 if su else 0)
            form.addRow("Watchdog time (ms)", self.wd)
            form.addRow("Bus startup", self.startup)
            form.addRow("Byte order", self.endian)
            ios = model.raw.get("pn_io_state")
            if ios is not None:                  # IO State Information (Producer) — read-only
                lbl = QLabel({0: "Disabled", 1: "Bit", 2: "Byte"}.get(ios, str(ios)))
                lbl.setObjectName("Dim")
                form.addRow("IO state (read-only)", lbl)

        # EtherCAT: SyCon "General Settings" scalars (Stufe 1), all written byte-exact
        # to the export .nxd + configMD5. Ident is shown read-only (device identity).
        self._ec = (model.raw.get("protocol_kind") == "ethercat")
        self.ec = {}
        g = model.raw.get("ec_general") if self._ec else None
        if g:
            self.ec["wd"] = QSpinBox(); self.ec["wd"].setRange(0, 0xFFFFFF)
            self.ec["wd"].setValue(int(g.get("watchdog_ms", 0)))
            self.ec["startup"] = QComboBox()
            self.ec["startup"].addItems(["Automatically by device",
                                         "Controlled by application"])
            self.ec["startup"].setCurrentIndex(1 if g.get("bus_startup") else 0)
            self.ec["sync"] = QSpinBox(); self.ec["sync"].setRange(0, 0xFFFF)
            self.ec["sync"].setValue(int(g.get("sync_x10ns", 0)))
            self.ec["alias"] = QSpinBox(); self.ec["alias"].setRange(0, 0xFFFF)
            self.ec["alias"].setValue(int(g.get("station_alias", 0)))
            form.addRow("Watchdog time (ms)", self.ec["wd"])
            form.addRow("Bus startup", self.ec["startup"])
            form.addRow("SyncImpulseLength (×10 ns)", self.ec["sync"])
            form.addRow("Station alias", self.ec["alias"])
            if "io_data_status" in g:        # blob-only (no .nxd / robot effect)
                self.ec["ios"] = QComboBox()
                self.ec["ios"].addItems(["None", "1 Byte", "2 Bytes",
                                         "3 Bytes", "4 Bytes"])
                iv = int(g.get("io_data_status") or 0)
                self.ec["ios"].setCurrentIndex(iv if 0 <= iv < self.ec["ios"].count() else 0)
                form.addRow("I/O data status", self.ec["ios"])
            ident = QLabel(f"Vendor 0x{g.get('vendor_id', 0):X} · "
                           f"Product 0x{g.get('product_code', 0):X} · "
                           f"Rev 0x{g.get('revision', 0):X} · "
                           f"Serial 0x{g.get('serial', 0):X}")
            ident.setObjectName("Dim")
            form.addRow("Ident (read-only)", ident)
        root.addLayout(form)
        if self._pn:
            note = QLabel("Device name -> _nwid.nxd + Val3 + SyCon project (names up to "
                          "the current length; longer names update the robot files only). "
                          "Watchdog, bus startup and byte order are written to main.nxd "
                          "+ the SyCon project + configMD5. (I/O status info is structural "
                          "— not here yet.) Re-validate in SyCon.net before download.")
        else:
            note = QLabel("Written to the Val3 export and the SyCon project (IP / name "
                          "patched in place). Re-validate in SyCon.net before download.")
        note.setObjectName("Dim")
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch()

    def apply(self):
        from fbconfig.protocols import ethernetip as eip
        val = self.edit.text().strip() if self.edit is not None else ""
        kind = self.fields.get("kind")
        new_station = None
        if val:
            if kind == "ip" and not valid_ip(val):
                raise ValueError("Invalid IP address (expected a.b.c.d, 0–255).")
            self.fields[{"ip": "ip", "name": "name",
                         "station": "station"}.get(kind, "raw")] = val
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
        elif not self._ec:                       # EtherCAT may edit only the scalars
            raise ValueError("Value must not be empty.")
        extra = ""
        if self._pn and self.wd is not None:
            self.model.raw["pn_watchdog"] = self.wd.value()
            self.model.raw["pn_endian_big"] = (self.endian.currentIndex() == 0)
            self.model.raw["pn_startup"] = self.startup.currentIndex()   # 0=auto,1=app
            extra = (f", watchdog {self.wd.value()} ms, "
                     f"{'big' if self.endian.currentIndex() == 0 else 'little'} endian, "
                     f"startup {'app' if self.startup.currentIndex() else 'auto'}")
        if self._ec and self.ec:
            g = dict(self.model.raw.get("ec_general") or {})
            g["watchdog_ms"] = self.ec["wd"].value()
            g["bus_startup"] = self.ec["startup"].currentIndex()   # 0=auto,1=app
            g["sync_x10ns"] = self.ec["sync"].value()
            g["station_alias"] = self.ec["alias"].value()
            if "ios" in self.ec:
                g["io_data_status"] = self.ec["ios"].currentIndex()
            self.model.raw["ec_general"] = g
            extra = (f", watchdog {g['watchdog_ms']} ms, startup "
                     f"{'app' if g['bus_startup'] else 'auto'}, "
                     f"sync {g['sync_x10ns']}, alias {g['station_alias']}")
        return f"Network identity '{new_station or '(unchanged)'}'{extra}."


class ModuleForm(InspectorForm):
    """Add a PROFINET module from the GSDML catalog. On apply it runs the byte-exact
    module compiler (blob_pn.add_catalog_module) on the project's SyCon blob + main.nxd
    and writes them (with a backup); the window then reloads so the module + its signal
    appear. The module is a contract with the PLC — the same module-ID must sit in the
    same slot on the controller side."""

    def __init__(self, model, direction=None, slot=None):
        super().__init__()
        self.model = model
        self.paths = model.raw["paths"]
        from fbconfig import gsdml
        self.catalog = gsdml.catalog()
        # When invoked from a table (Add on empty space), only offer modules of THAT
        # direction — the user already pointed at Eingänge or Ausgänge.
        self.pn_direction = direction          # so the window keeps that table active
        self._dir = {"In": "input", "Out": "output"}.get(direction)
        if self._dir:
            self.catalog = [m for m in self.catalog if m.direction == self._dir]
        self.used_slots = self._existing_slots()
        nxt = slot if (slot and slot not in self.used_slots) else \
            (max(self.used_slots) + 1 if self.used_slots else 1)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        dlbl = {"input": "Inputs", "output": "Outputs"}.get(self._dir, "")
        sub = QLabel(f"New slot{f' ({dlbl})' if dlbl else ''}: pick the size and the "
                     "slot number. Writes the SyCon project + .nxd byte-exact; put the "
                     "same module-ID in this slot on the PLC.")
        sub.setObjectName("Dim")
        sub.setWordWrap(True)
        root.addWidget(sub)
        form = QFormLayout()
        form.setSpacing(10)
        self.combo = QComboBox()
        for m in self.catalog:
            self.combo.addItem(f"{en_label(m.name)}  ({m.size} B "
                               f"{'In' if m.direction == 'input' else 'Out'})", m)
        form.addRow("Size", self.combo)
        self.slot = QSpinBox()
        self.slot.setRange(1, 99)
        self.slot.setValue(nxt)
        form.addRow("Slot", self.slot)
        self.slot_warn = QLabel()
        self.slot_warn.setStyleSheet(f"color: {theme.ERR}; font-weight: 600;")
        self.slot_warn.setWordWrap(True)
        self.slot_warn.hide()
        form.addRow("", self.slot_warn)
        self.label = QLineEdit()
        self.label.setPlaceholderText("default: Inputs / Outputs")
        form.addRow("Signal label", self.label)
        root.addLayout(form)
        if not (self.paths.nxd and self.paths.nxd.is_file()):
            warn = QLabel("⚠ No exported .nxd found — only the SyCon project will be "
                          "written.")
            warn.setObjectName("Dim")
            warn.setWordWrap(True)
            root.addWidget(warn)
        if self.used_slots:
            used = QLabel("Used slots: " + ", ".join(str(s) for s in sorted(self.used_slots)))
            used.setObjectName("Dim")
            used.setWordWrap(True)
            root.addWidget(used)
        root.addStretch()
        self.combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.slot.valueChanged.connect(self._on_slot)
        self._on_slot()

    def _on_slot(self, *_):
        taken = self.slot.value() in self.used_slots
        self.slot_warn.setVisible(taken)
        if taken:
            self.slot_warn.setText(f"✕ Slot {self.slot.value()} is already used — "
                                   "pick a free slot.")
        self.changed.emit()

    def _existing_slots(self):
        """User slot numbers already present in the device XML (Slot 0 is the head)."""
        import re
        from fbconfig import sycon
        try:
            blob = sycon.blob_from_xml(self.paths.sycon_xml.read_text("utf-8", "replace"))
            return {int(s) for s in re.findall(r'moduleAddress="Slot (\d+)"',
                    blob.decode("utf-16-le", "ignore")) if int(s) > 0}
        except Exception:
            return set()

    def is_valid(self):
        return self.slot.value() not in self.used_slots

    def apply(self):
        from datetime import datetime
        from fbconfig import blob_pn, backup
        mod = self.combo.currentData()
        slot = self.slot.value()
        if slot in self.used_slots:
            raise ValueError(f"Slot {slot} is already used — pick a free slot.")
        label = self.label.text().strip() or None
        base_xml = self.paths.sycon_xml.read_text("utf-8", "replace")
        base_nxd = self.paths.nxd.read_bytes() if (self.paths.nxd and self.paths.nxd.is_file()) else None
        if base_nxd is None:
            raise ValueError("No exported .nxd to compile into — export the config "
                             "once in SyCon first, then modules can be added.")
        # a new slot starts EMPTY (signals=[]) -> the table shows free bytes the user
        # then fills, rather than one pre-filled whole-module signal.
        new_xml, new_nxd = blob_pn.add_catalog_module(base_xml, base_nxd,
                                                      mod.module_ident, slot, label,
                                                      signals=[])
        backup.make_backup(self.paths, datetime.now())
        self.paths.sycon_xml.write_text(new_xml, encoding="utf-8")
        self.paths.nxd.write_bytes(new_nxd)
        self.reload_after = True
        return f"Added empty Slot {slot} ({mod.name})."


# Signal data types, in SyCon's "Edit Signal" order (the netX device-XML dataType
# strings); value = bit width. Mirrors SyCon's New-Type dropdown.
_SIG_BITS = {"bit": 1, "byte": 8, "signed8": 8, "unsigned8": 8,
             "word": 16, "signed16": 16, "unsigned16": 16,
             "dword": 32, "signed32": 32, "unsigned32": 32, "real32": 32}
_SIG_DTYPES = list(_SIG_BITS.items())


def _type_label(dtype: str) -> str:
    """Name-prefix label for a data type with the bit-width digits stripped, so the
    auto-suggested prefix reads 'In_real_' not 'In_real32_' (real32->real, signed8->
    signed, unsigned16->unsigned; bit/byte/word/dword unchanged). Applies to every data
    type and every bus protocol's add/edit form."""
    return dtype.rstrip("0123456789") or dtype


def _derive_naming(names):
    """(prefix, numbering-start, digits) inferred from existing signal names so a
    multi-row Edit pre-fills exactly the selection's scheme. Uses the FIRST name's
    trailing digit run (e.g. 'In_byte_03' -> 'In_byte_', start 3, digits 2); without a
    trailing number, falls back to the common prefix + start 0."""
    import re
    import os
    if not names:
        return "", 0, 1
    m = re.search(r"(\d+)$", names[0])
    if m:
        return names[0][:m.start()], int(m.group(1)), len(m.group(1))
    return os.path.commonprefix(names), 0, 1


class ModuleEditForm(InspectorForm):
    """Edit a PROFINET module: change its SIZE (swap catalog module) and/or SLOT number.
    Rebuilds the whole module set (delete-all + re-add, byte-exact) keeping the order and
    every signal's UID; signals that no longer fit a smaller size are dropped (warned)."""

    def __init__(self, model, module):
        super().__init__()
        self.model = model
        self.paths = model.raw["paths"]
        self.module = module                       # parse_modules dict (slot, size, …)
        from fbconfig import gsdml
        self.dirn = module["direction"]
        self.pn_direction = "In" if self.dirn == "input" else "Out"
        self.sizes = sorted({m.size for m in gsdml.catalog()
                             if m.direction == self.dirn})
        self.used = self._other_slots()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        sub = QLabel(f"Slot {module['slot']} · {en_label(module['module_type'])}. Change size or "
                     "slot number; the project + .nxd are rebuilt byte-exact and signal "
                     "UIDs are kept.")
        sub.setObjectName("Dim")
        sub.setWordWrap(True)
        root.addWidget(sub)
        form = QFormLayout()
        form.setSpacing(10)
        self.size = QComboBox()
        for s in self.sizes:
            self.size.addItem(f"{s} Byte", s)
        i = self.size.findData(module["size"])
        if i >= 0:
            self.size.setCurrentIndex(i)
        form.addRow("Size", self.size)
        self.slot = QSpinBox()
        self.slot.setRange(1, 99)
        self.slot.setValue(module["slot"])
        form.addRow("Slot", self.slot)
        root.addLayout(form)
        self.note = QLabel()
        self.note.setObjectName("Dim")
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        self.warn = QLabel()
        self.warn.setStyleSheet(f"color: {theme.ERR}; font-weight: 600;")
        self.warn.setWordWrap(True)
        self.warn.hide()
        root.addWidget(self.warn)
        root.addStretch()
        self.size.currentIndexChanged.connect(self._recompute)
        self.slot.valueChanged.connect(self._recompute)
        self._recompute()

    def _other_slots(self):
        from fbconfig import blob_pn
        try:
            xml = self.paths.sycon_xml.read_text("utf-8", "replace")
            return {m["slot"] for m in blob_pn.parse_modules(xml)
                    if m["slot"] != self.module["slot"]}
        except Exception:
            return set()

    def _kept_dropped(self):
        size = self.size.currentData()
        kept, dropped = [], []
        for s in self.module["signals"]:
            w = _SIG_BITS[s["dtype"]] * s.get("arr", 1)
            end = s["byte"] * 8 + (s.get("bit", 0) if s["dtype"] == "bit" else 0) + w
            (kept if end <= size * 8 else dropped).append(s)
        return kept, dropped

    def _recompute(self, *_):
        kept, dropped = self._kept_dropped()
        self.note.setText(f"{len(kept)} signal(s) kept" +
                          (f", {len(dropped)} dropped (don't fit the smaller size)"
                           if dropped else ""))
        taken = self.slot.value() in self.used
        self.warn.setVisible(taken)
        if taken:
            self.warn.setText(f"✕ Slot {self.slot.value()} is already used.")
        self.changed.emit()

    def is_valid(self):
        return self.slot.value() not in self.used

    def apply(self):
        from datetime import datetime
        from fbconfig import blob_pn, backup, gsdml
        size = self.size.currentData()
        slot = self.slot.value()
        if slot in self.used:
            raise ValueError(f"Slot {slot} is already used — pick a free slot.")
        cm = next((m for m in gsdml.catalog()
                   if m.size == size and m.direction == self.dirn), None)
        if cm is None:
            raise ValueError(f"No GSDML module for {size} B {self.dirn}.")
        nxd = (self.paths.nxd.read_bytes()
               if (self.paths.nxd and self.paths.nxd.is_file()) else None)
        if nxd is None:
            raise ValueError("No exported .nxd to rebuild — export once in SyCon first.")
        xml = self.paths.sycon_xml.read_text("utf-8", "replace")
        specs = blob_pn.capture_modules(xml)
        kept, dropped = self._kept_dropped()
        for sp in specs:                           # replace THIS module's spec in place
            if sp["slot"] == self.module["slot"]:
                sp["slot"] = slot
                sp["module_ident"] = cm.module_ident
                sp["signals"] = [dict(s) for s in kept]
                break
        specs.sort(key=lambda s: s["slot"])        # re-sort so device order = slot order
        new_xml, new_nxd = blob_pn.rebuild_modules(xml, nxd, specs)
        backup.make_backup(self.paths, datetime.now())
        self.paths.sycon_xml.write_text(new_xml, encoding="utf-8")
        if new_nxd is not None and self.paths.nxd:
            self.paths.nxd.write_bytes(new_nxd)
        self.reload_after = True
        return (f"Module now Slot {slot} · {size} Byte"
                + (f" ({len(dropped)} signal(s) dropped)." if dropped else "."))


class PnSignalForm(InspectorForm):
    """Add signal(s) INSIDE one PROFINET module (slot) — POWERLINK-style: pick a start
    byte, a data type and a count/array length; the target range is highlighted live in
    the table and turns red if it leaves the slot or overlaps an existing signal. A
    signal may NEVER cross the slot boundary. Bits are byte-aligned: 'separate' bits
    come in whole bytes (8, 16, …), a bit 'array' length is counted in bytes (×8).
    Writes only the device XML (nxd + per-slot streams untouched); window reloads."""

    # (direction, global_start_byte, n_bytes, fits) — live landing preview
    preview = Signal(str, int, int, bool)

    def __init__(self, model, cfg, module, direction, start_byte=0, edit=None,
                 span_bytes=0, edit_set=None):
        super().__init__()
        self.model = model
        self.cfg = cfg
        self.paths = model.raw["paths"]
        self.module = module                       # parse_modules dict for this slot
        self.dirn = direction                      # "In" / "Out"
        self.edit = edit                           # single signal dict being edited
        self.edit_set = list(edit_set) if edit_set else None  # several signals -> rebuild
        self._span_bytes = span_bytes              # selected byte span -> default count
        self._edit_uids = []                       # UIDs to keep, in order (multi-edit)
        # The flat protocols (EtherCAT / EtherNet/IP / POWERLINK) reuse this EXACT page —
        # the whole direction is one synthetic slot; only the header text + apply() target
        # differ (model rebuild vs the PROFINET blob writer). PROFINET keeps the slot model.
        pk = model.raw.get("protocol_kind")
        self.is_pn = pk == "profinet"
        self.is_flat = not self.is_pn          # model-rebuild protocols
        self.is_pl = pk == "powerlink"         # byte-addressed (no bit_offset / repack)
        self.is_ec = self.is_flat              # back-compat alias (slot wording etc.)
        self._pname = {"ethercat": "EtherCAT", "ethernetip": "EtherNet/IP",
                       "powerlink": "POWERLINK"}.get(pk, "")
        self.iface = (model.inp if direction == "In" else model.out) if self.is_flat else None
        self._cur = [dict(s) for s in module["signals"]]
        if edit is not None:                       # editing: drop the original; re-add
            self._cur = [s for s in self._cur if s is not edit and not (
                s["byte"] == edit["byte"] and s.get("bit", 0) == edit.get("bit", 0)
                and s["dtype"] == edit["dtype"] and s["name"] == edit["name"])]
        elif self.edit_set:                        # multi-edit: drop the whole set by UID
            _drop = {s.get("uid") for s in self.edit_set}
            self._cur = [s for s in self._cur if s.get("uid") not in _drop]
        self._ok = False
        self._build()
        self.startb.setMaximum(max(0, module["size"] - 1))
        if edit is not None:
            self.dtype.setCurrentText(edit["dtype"])
            arr = edit.get("arr", 1)
            if arr > 1:
                self.arr_cb.setChecked(True)
                self.count.setValue(arr // 8 if edit["dtype"] == "bit" else arr)
            self.startb.setValue(min(edit["byte"], module["size"] - 1))
            self.prefix.setText(edit["name"])
            self._prefix_touched = True            # keep the edited signal's name as-is
        elif self.edit_set:
            # several homogeneous, contiguous signals -> pre-fill the full page from the
            # selection (type / count / start / name scheme), all editable; UIDs travel.
            sset = sorted(self.edit_set, key=lambda s: (s["byte"], s.get("bit", 0)))
            self._edit_uids = [s.get("uid") for s in sset]
            self.dtype.setCurrentText(sset[0]["dtype"])
            self.arr_cb.setChecked(False)          # separate values
            self.count.setValue(len(sset))         # initial = the selection's signal count
            self._span_bytes = self._footprint_bytes(sset)   # type change re-derives count
            self.startb.setValue(min(sset[0]["byte"], module["size"] - 1))
            pfx, ns, dg = _derive_naming([s["name"] for s in sset])
            self.prefix.setText(pfx)
            self._prefix_touched = True            # keep the selection's prefix
            self.nstart.setValue(ns)
            self.digits.setValue(dg)
        else:
            self.startb.setValue(min(start_byte, module["size"] - 1))
            if self._span_bytes > 1:               # multi-byte selection -> default count
                self._apply_span()
        self._recompute()

    @staticmethod
    def _footprint_bytes(sigs):
        """Byte span covered by a (homogeneous, contiguous) signal set — the LENGTH a
        multi-edit hands to the count when the data type is switched."""
        bits = sum(_SIG_BITS[s["dtype"]] * (s.get("arr", 1) or 1) for s in sigs)
        return max(1, (bits + 7) // 8)

    def _apply_span(self):
        """Take the selected byte span as the LENGTH: count = span ÷ type width (floor;
        the leftover is flagged in _recompute). Bits fill the span (×8)."""
        dt = self._dt()
        if dt == "bit":
            self.count.setValue(max(1, self._span_bytes * 8))
            return
        each = _SIG_BITS[dt] // 8
        self.count.setValue(max(1, self._span_bytes // each))

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        m = self.module
        _io = 'Inputs' if m['direction'] == 'input' else 'Outputs'
        head = QLabel(f"{self._pname} · {m['size']} B ({_io})" if self.is_flat else
                      f"Slot {m['slot']} · {en_label(m['module_type'])} · {m['size']} B ({_io})")
        head.setObjectName("Dim")
        head.setWordWrap(True)
        root.addWidget(head)

        form = QFormLayout()
        form.setSpacing(10)
        self.startb = QSpinBox()
        self.startb.setToolTip("First byte the signal occupies." if self.is_ec else
                               "First byte WITHIN the slot the signal occupies.")
        # EtherCAT has no slots -> just "Start byte"; PROFINET keeps "(in slot)" (the byte is
        # relative to the slot, not the global image).
        form.addRow("Start byte" if self.is_ec else "Start byte (in slot)", self.startb)
        self.dtype = QComboBox()
        self.dtype.addItems([n for n, _ in _SIG_DTYPES])
        self.dtype.setCurrentText(self.cfg.get("last_type", "byte")
                                  if self.cfg.get("last_type") in _SIG_BITS else "byte")
        form.addRow("Data type", self.dtype)

        # binary choice -> a single checkbox (cleaner + clearer than a 2-item dropdown):
        # unchecked = separate values, checked = one array signal.
        self.arr_cb = QCheckBox("Combine into one array signal")
        form.addRow("How to add", self.arr_cb)

        self.count = QSpinBox()
        self.count.setRange(1, 9999)
        self.count_lbl = QLabel("Count")
        form.addRow(self.count_lbl, self.count)
        self.prefix = QLineEdit()
        self._prefix_touched = getattr(self, "_prefix_touched", False)
        self.prefix.textEdited.connect(self._mark_prefix_touched)   # only USER edits
        form.addRow("Name / prefix", self.prefix)
        self.nstart = QSpinBox()
        self.nstart.setRange(0, 99999)
        self.nstart.setValue(self.cfg["naming"]["start"])
        form.addRow("Numbering start", self.nstart)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setValue(self.cfg["naming"]["digits"])
        form.addRow("Digits (zero-pad)", self.digits)
        root.addLayout(form)

        self.info = QLabel()
        self.info.setObjectName("Mono")
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color: {theme.ERR}; font-weight: 600;")
        self.warn.hide()
        root.addWidget(self.warn)
        root.addStretch()

        self.dtype.currentIndexChanged.connect(self._on_type_changed)
        self.arr_cb.toggled.connect(self._recompute)
        for sb in (self.startb, self.count):
            sb.valueChanged.connect(self._recompute)

    def _on_type_changed(self, *_):
        # the selected byte LENGTH stays fixed; the count follows the new data type
        # (4 B selected -> byte 4, bit 32, word 2, real32 1). Only when a span/selection
        # drove the count (plain Add without a selection keeps the user's count).
        if self._span_bytes > 0:
            self._apply_span()                     # setValue -> _recompute fires
        self._recompute()

    def _mark_prefix_touched(self, *_):
        self._prefix_touched = True

    def _is_sep(self):
        return not self.arr_cb.isChecked()         # unchecked = separate values

    def _dt(self):
        return self.dtype.currentText()

    def _is_bit(self):
        return self._dt() == "bit"

    def _nbytes(self):
        """Byte footprint inside the slot for the current selection."""
        n = self.count.value()
        if self._is_bit():
            # separate: n single bits packed into whole bytes; array: n bytes (×8 bits)
            return (n + 7) // 8 if self._is_sep() else n
        each = _SIG_BITS[self._dt()] // 8
        return n * each

    def _occupied(self):
        bits = set()
        for s in self._cur:
            w = _SIG_BITS[s["dtype"]] * s.get("arr", 1)
            base = s["byte"] * 8 + (s["bit"] if s["dtype"] == "bit" else 0)
            bits.update(range(base, base + w))
        return bits

    def _recompute(self, *_):
        m = self.module
        dt = self._dt()
        sep = self._is_sep()
        if self._is_bit():
            self.count_lbl.setText("Count (bits, ×8)" if sep else "Array length (bytes)")
        else:
            self.count_lbl.setText("Count" if sep else "Array length")
        # auto-suggest a name prefix from direction + data type, and KEEP it in sync with the
        # type — until the user edits it themselves (then leave their text alone).
        if not self._prefix_touched:
            io = 'In' if m['direction'] == 'input' else 'Out'
            self.prefix.setText(f"{io}_{_type_label(dt)}_")

        start = self.startb.value()
        nbytes = self._nbytes()
        new_bits = nbytes * 8
        base_bit = start * 8
        bit_ok = (not self._is_bit()) or (not sep) or (self.count.value() % 8 == 0)
        in_slot = start + nbytes <= m["size"]
        overlap = bool(self._occupied() & set(range(base_bit, base_bit + new_bits)))
        self._ok = nbytes > 0 and in_slot and not overlap and bit_ok

        free = m["size"] - len(self._occupied()) // 8
        where = "interface" if self.is_ec else "slot"
        if self.is_ec:
            self.info.setText(
                f"{m['size']} B {where}, ~{free} B free. New data: bytes "
                f"{start}–{start + nbytes - 1} ({nbytes} B).")
        else:
            self.info.setText(
                f"Slot {m['slot']}: {m['size']} B, ~{free} B free. "
                f"New data: bytes {start}–{start + nbytes - 1} of the slot "
                f"({nbytes} B) → global {m['global_start'] + start}–"
                f"{m['global_start'] + start + nbytes - 1}.")

        if self._span_bytes > 1 and not self._is_bit():
            each = _SIG_BITS[dt] // 8
            if self._span_bytes % each:            # selection didn't divide evenly
                self.info.setText(self.info.text() +
                                  f"  ⚠ {self._span_bytes} B selection is not a "
                                  f"multiple of {each} B — count was rounded down.")

        msg = ""
        if not bit_ok:
            msg = "✕ Separate bits must come in whole bytes — use a count of 8, 16, …"
        elif not in_slot:
            msg = (f"✕ Runs past the {where} end (byte {start + nbytes} > {m['size']}). "
                   "Lower the count or pick an earlier start byte.")
        elif overlap:
            msg = f"✕ Overlaps an existing signal in this {where}. Pick a free byte."
        self.warn.setText(msg)
        self.warn.setVisible(bool(msg))

        gb = m["global_start"] + start
        self.preview.emit(self.dirn, gb, nbytes, self._ok)
        self.changed.emit()

    def is_valid(self):
        return self._ok

    def apply(self):
        from datetime import datetime
        from fbconfig import blob_pn, backup
        m = self.module
        dt = self._dt()
        start = self.startb.value()
        n = self.count.value()
        prefix = self.prefix.text().strip() or \
            f"{'In' if m['direction'] == 'input' else 'Out'}_{_type_label(dt)}_"
        ns, dg = self.nstart.value(), self.digits.value()
        self.cfg["naming"] = {"start": ns, "digits": dg}
        self.cfg["last_type"] = dt
        settings.save(self.cfg)
        scheme = NamingScheme(prefix, ns, dg)

        # a single separate signal keeps the prefix verbatim (so Edit / 'add one' set an
        # exact name); 2+ are numbered with the scheme.
        def sep_name(i, total):
            return (prefix.rstrip("_") or scheme.name(i)) if total == 1 else scheme.name(i)

        new = []
        if self._is_bit() and self._is_sep():
            for i in range(n):                       # n single bits, byte.bit
                new.append(dict(name=sep_name(i, n), dtype="bit",
                                byte=start + i // 8, bit=i % 8, arr=1))
        elif self._is_sep():
            each = _SIG_BITS[dt] // 8
            for i in range(n):                       # n separate values
                new.append(dict(name=sep_name(i, n), dtype=dt,
                                byte=start + i * each, bit=0, arr=1))
        else:                                        # one array signal
            arr = n * 8 if self._is_bit() else n
            name = prefix.rstrip("_") or f"{_type_label(dt)}_array"
            new.append(dict(name=name, dtype=dt, byte=start, bit=0, arr=arr))

        # assign each new signal its UID here (not at write time) so the window can
        # re-select exactly the new rows after reload; editing keeps the original
        # signal's UID so the PLC link survives a type/address change
        # ([[systemtag-must-travel]]).
        import uuid
        for s in new:
            s["uid"] = str(uuid.uuid4())
        if self.edit_set:
            # keep the selection's systemTags in order: same count -> all preserved,
            # more -> extras get fresh UIDs, fewer -> surplus UIDs drop. PLC links survive
            # ([[systemtag-must-travel]]).
            for i, s in enumerate(new):
                if i < len(self._edit_uids) and self._edit_uids[i]:
                    s["uid"] = self._edit_uids[i]
        elif self.edit is not None and new and self.edit.get("uid"):
            new[0]["uid"] = self.edit["uid"]
        self._cur.extend(new)
        if self.is_flat:
            # Flat protocols (EtherCAT / EtherNet/IP / POWERLINK): rebuild the MODEL (the
            # protocol writer compiles it on save) instead of writing the PROFINET blob.
            # Signals ordered by byte, the interface SIZE (max_bytes) kept (trailing free
            # allowed). UID travels. Bit-addressed protocols repack to bit offsets; POWERLINK
            # is byte-addressed (offsets derive from order, bit_offset stays None).
            from fbconfig.model import Signal as Sig
            st = "input" if self.dirn == "In" else "output"
            ordered = sorted(self._cur, key=lambda s: (s["byte"], s.get("bit", 0)))
            snap = list(self.iface.signals)
            self.iface.signals[:] = [
                Sig(name=s["name"], sycon_dtype=s["dtype"], array_elements=s.get("arr", 1) or 1,
                    systemtag=s["uid"], signal_type=st) for s in ordered]
            if self.is_pl:                       # byte-addressed: order defines the layout
                if self.iface.used_bytes > self.iface.max_bytes:
                    self.iface.signals[:] = snap
                    raise ValueError("Does not fit — grow the interface (Resize) first.")
            else:
                try:
                    self.iface.repack_bits()
                except ValueError as e:
                    self.iface.signals[:] = snap
                    raise ValueError(f"{e} Grow the interface (Resize) first.")
            self.model.raw["layout_dirty"] = True
            uids = {s["uid"] for s in new}
            self.added = [s for s in self.iface.signals if s.systemtag in uids]
            self.direction = self.iface.direction
            verb = ("Edited" if (self.edit is not None or self.edit_set)
                    else f"Added {len(new)}")
            return f"{verb} signal(s) in {self.iface.direction}. Re-validate in SyCon.net."
        self.pn_added_uids = [s["uid"] for s in new]
        self.pn_direction = self.dirn
        xml = self.paths.sycon_xml.read_text("utf-8", "replace")
        new_xml = blob_pn.write_module_signals(xml, m["slot"], self._cur,
                                               m["direction"], m["global_start"])
        backup.make_backup(self.paths, datetime.now())
        self.paths.sycon_xml.write_text(new_xml, encoding="utf-8")
        self.reload_after = True
        verb = ("Updated" if (self.edit is not None or self.edit_set)
                else f"Added {len(new)}")
        return (f"{verb} signal(s) in Slot {m['slot']}. "
                f"{len(self._cur)} signals in the slot now.")


def ec_signal_form(model, cfg, direction, start_byte=0, edit_sig=None,
                   span_bytes=0, edit_sigs=None):
    """Open the PROFINET signal page (the SAME PnSignalForm) for a FLAT protocol — EtherCAT /
    EtherNet/IP / POWERLINK — so the GUI + data-type selection are identical everywhere. The
    whole direction is one synthetic slot (size = interface budget); PnSignalForm rebuilds the
    model on apply. Byte positions come from each signal's bit offset (bit-addressed) or its
    byte offset (POWERLINK). `edit_sig` = the model Signal being edited (UID preserved)."""
    iface = model.inp if direction == "In" else model.out
    byte_mode = model.raw.get("protocol_kind") == "powerlink"   # no bit_offset
    sigs = []
    for i, s in enumerate(iface.signals):
        if byte_mode:
            byte, bit = iface.byte_offset(i), 0
        else:
            bo = s.bit_offset or 0
            byte, bit = bo // 8, bo % 8
        sigs.append(dict(name=s.name, dtype=s.sycon_dtype, byte=byte, bit=bit,
                         arr=s.array_elements, uid=s.systemtag))
    module = dict(slot=0, module_type=("Inputs" if direction == "In" else "Outputs"),
                  size=iface.max_bytes,
                  direction="input" if direction == "In" else "output",
                  global_start=0, signals=sigs)
    edit = None
    if edit_sig is not None:
        edit = next((d for d in sigs if d["uid"] == edit_sig.systemtag), None)
    edit_set = None
    if edit_sigs:
        want = {s.systemtag for s in edit_sigs}
        edit_set = [d for d in sigs if d["uid"] in want]
    return PnSignalForm(model, cfg, module, direction, start_byte=start_byte,
                        edit=edit, span_bytes=span_bytes, edit_set=edit_set)


# ------------------------------------------------------- Add (EtherNet/IP bits)
class EipAddForm(InspectorForm):
    """Add bit-granular signals to an EtherNet/IP or EtherCAT direction; only data
    types that already exist are offered (a write template + the project's
    arrayElements convention are cloned). Inserts, re-packs bit offsets, reverts on
    overflow."""

    BITS = {"bit": 1, "signed8": 8, "unsigned8": 8, "byte": 8, "signed16": 16,
            "word": 16, "signed32": 32, "real32": 32, "unsigned16": 16,
            "unsigned32": 32, "dword": 32}

    def __init__(self, model, cfg, available_types, direction=None):
        super().__init__()
        self.model = model
        self.cfg = cfg
        self._ok = False
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)
        self.dir = QComboBox()
        self.dir.addItems(["In", "Out"])
        self.dir.setCurrentText(direction or "In")
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
        self._prefix_touched = False
        self.prefix.textEdited.connect(lambda *_: setattr(self, "_prefix_touched", True))
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
        root.addStretch()

        for w in (self.dir, self.dtype):
            w.currentIndexChanged.connect(self._recompute)
        self.pos.currentIndexChanged.connect(self._recompute)
        self.count.valueChanged.connect(self._update_info)
        self._recompute()

    def _iface(self):
        return self.model.inp if self.dir.currentText() == "In" else self.model.out

    def _free_bits(self):
        iface = self._iface()
        # Both EtherCAT and EtherNet/IP have a fixed interface size (budget); Add fills the
        # FREE bytes. Grow the interface first via Resize to make more room.
        return iface.max_bytes * 8 - iface.used_bits

    def _template(self):
        """An existing signal of the chosen dtype — its arrayElements/bits define
        the project's convention (per-bit ae=1 vs byte-packed ae=8). For a dtype not
        yet in the config the catalog default is used (bit -> 8 flags, else 1)."""
        dn = self.dtype.currentText()
        return next((s for s in self.model.inp.signals + self.model.out.signals
                     if s.sycon_dtype == dn), None)

    def _ae(self):
        """arrayElements for a new signal: clone the project's convention if a
        same-dtype signal exists, else the catalog default (bit=8 byte-packed)."""
        t = self._template()
        return t.array_elements if t else by_sycon(self.dtype.currentText()).array_elements

    def _per(self):
        """Bits one new signal occupies (dtype x arrayElements)."""
        return bit_width(self.dtype.currentText(), self._ae())

    def _recompute(self, *_):
        iface = self._iface()
        insert = self.pos.currentIndex() == 1 and bool(iface.signals)
        self.idx.setEnabled(insert)
        self.idx.setRange(0, max(0, len(iface.signals) - 1))
        dn = self.dtype.currentText()
        if not self._prefix_touched:        # keep in sync with the type until user-edited
            self.prefix.setText(f"{iface.direction}_{_type_label(dn)}_")
        self.count.setMaximum(max(1, self._free_bits() // self._per()))
        self._update_info()

    def _update_info(self, *_):
        dn = self.dtype.currentText()
        per = self._per()
        free = self._free_bits()
        self.info.setText(
            f"{self._iface().direction}: {free} free bit(s) ({free // 8} byte). "
            f"One {dn} = {per} bit. Multi-byte types are byte-aligned, so actual "
            f"capacity may be a little lower (checked on add).")
        self._ok = free >= per
        self.changed.emit()

    def is_valid(self):
        return self._ok

    def apply(self):
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
        ae = self._ae()                          # project convention or catalog default
        new = [Sig(scheme.name(i), dn, array_elements=ae, signal_type=st)
               for i in range(self.count.value())]
        snap = list(iface.signals)
        if self.model.raw.get("protocol_kind") == "profinet":
            # place into free bytes within modules (no repack)
            from fbconfig.protocols import ethernetip as eip
            try:
                eip.pn_add(self.model, st, new)
            except ValueError as e:
                iface.signals[:] = snap
                raise ValueError(str(e))
        else:
            for k, s in enumerate(new):
                iface.signals.insert(index + k, s)
            try:
                iface.repack_bits()
            except ValueError as e:
                iface.signals[:] = snap              # revert on overflow
                raise ValueError(str(e))
        self.model.raw["layout_dirty"] = True
        self.added = new
        self.direction = iface.direction
        return (f"Added {len(new)} × {dn} to {iface.direction}. "
                "Re-validate in SyCon.net before download.")

# ==================================================================== the panel
class InspectorPanel(QFrame):
    """Right-docked container that shows one InspectorForm at a time."""

    applied = Signal(str)        # emitted with the summary after a successful apply
    closed = Signal()            # emitted when the panel hides (apply or cancel)
    opened = Signal()            # emitted when a form is shown (freeze the tables)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Inspector")
        self.setFixedWidth(360)
        self.setVisible(False)
        self.current_form: InspectorForm | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = QWidget()
        head.setObjectName("InspectorHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 12, 10, 12)
        self.title = QLabel("")
        self.title.setObjectName("H2")
        hl.addWidget(self.title)
        hl.addStretch()
        close = QPushButton("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(26, 26)
        close.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 0; color: {theme.TEXT_DIM};"
            f" font-size: 13px; }} QPushButton:hover {{ color: {theme.TEXT}; }}")
        close.clicked.connect(self.close_form)
        hl.addWidget(close)
        v.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        v.addWidget(self.scroll, 1)

        self.err = QLabel()
        self.err.setStyleSheet(f"color: {theme.ERR}; padding: 0 16px;")
        self.err.setWordWrap(True)
        self.err.hide()
        v.addWidget(self.err)

        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 12, 16, 14)
        fl.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close_form)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.clicked.connect(self._do_apply)
        fl.addWidget(self.cancel_btn)
        fl.addWidget(self.apply_btn)
        v.addWidget(foot)

    def open(self, form: InspectorForm, title: str, apply_text: str = "Apply"):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.current_form = form
        self.title.setText(title.upper())
        self.apply_btn.setText(apply_text)
        self.scroll.setWidget(form)
        form.changed.connect(self._refresh_validity)
        self.err.hide()
        self._refresh_validity()
        self.setVisible(True)
        self.apply_btn.setFocus()
        self.opened.emit()

    def _refresh_validity(self):
        if self.current_form is not None:
            self.apply_btn.setEnabled(self.current_form.is_valid())

    def _do_apply(self):
        if self.current_form is None:
            return
        try:
            summary = self.current_form.apply()
        except Exception as e:
            # ValueErrors are normal validation messages; anything else (e.g. a blob
            # parse failure) also gets a full diagnostic log next to the project file.
            msg = str(e)
            paths = getattr(self.current_form, "paths", None)
            if not isinstance(e, ValueError) or "length prefix" in msg:
                try:
                    from fbconfig import pndiag
                    from datetime import datetime
                    rd = paths.sycon_xml.parent if paths else None
                    log = pndiag.log_failure(rd, paths, "Inspector apply failed", e,
                                             datetime.now())
                    msg = f"{msg}  ·  debug log: {log}"
                except Exception:
                    pass
            self.err.setText(msg)
            self.err.show()
            return
        self.applied.emit(summary or "")

    def close_form(self):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.current_form = None
        self.err.hide()
        self.setVisible(False)
        self.closed.emit()
