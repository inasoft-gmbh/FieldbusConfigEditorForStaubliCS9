"""Main window: overview cards + In/Out signal tables + full edit toolbar.

Thin client over `fbconfig`. Mirrors every console action and adds GUI-only
conveniences: multi-select (Shift/Ctrl) delete & rename, per-byte display of
free space, and drag-and-drop reordering within an interface.
"""
from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import (Qt, Signal, QVariantAnimation, QEasingCurve,
                            QItemSelection, QItemSelectionModel, QMimeData,
                            QPoint, QRect)
from PySide6.QtGui import (QAction, QKeySequence, QColor, QBrush, QDrag,
                           QPixmap, QPainter, QIcon)

from fbconfig.paths import asset
from gui.winutil import dark_titlebar
from gui.toast import ToastManager
from gui.command_palette import CommandPalette
from gui.inspector import (InspectorPanel, AddForm, ResizeForm, GeneralForm,
                          StationForm, EipAddForm, ec_signal_form, ModuleForm,
                          PnSignalForm, ModuleEditForm, en_label)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QToolBar, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QSplitter,
    QAbstractItemView, QSizePolicy,
)

from fbconfig import project, settings, save as savemod
from fbconfig.datatypes import by_sycon, CATALOG
from fbconfig.protocols import ethernetip as eip
from . import theme
from .dialogs import ProjectPicker, BatchRenameDialog, NewConfigDialog
from gui.overlay import Overlay, AboutCard, SafetyCard, ConfirmCard


class AppState:
    def __init__(self):
        self.robot_dir: str | None = None
        self.paths = None
        self.model = None

    @property
    def loaded(self) -> bool:
        return self.model is not None


# --------------------------------------------------------------- small widgets
def _card(title: str) -> tuple[QWidget, QGridLayout]:
    w = QWidget()
    w.setObjectName("Card")
    outer = QVBoxLayout(w)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(10)
    head = QLabel(title)
    head.setObjectName("H2")
    outer.addWidget(head)
    grid = QGridLayout()
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(6)
    outer.addLayout(grid)
    return w, grid


class SignalTable(QTableWidget):
    """Signal table with multi-select and internal drag-and-drop reordering.

    Drag-drop is purely a view affair: on drop we emit `rowsDropped` with the
    selected signal indices and the target signal index, and let the window
    mutate the model + re-render. The default Qt row move is suppressed.
    """

    # (signal_indices, target_addr, onto_free, target_sig_index)
    rowsDropped = Signal(list, int, bool, int)
    # PROFINET drag: (list of source signal rowmeta, target rowmeta) — move keeping UID
    pnRowsDropped = Signal(object, object)
    # PROFINET module drag: (source slot, target slot) — reorder whole modules
    pnModuleDropped = Signal(int, int)
    # (signal_index, new_name) — emitted when an in-cell rename is committed
    nameEdited = Signal(int, str)
    # PROFINET in-cell rename: (signal rowmeta, new_name)
    pnNameEdited = Signal(object, str)
    # the user clicked/focused this table — lets Add target the right direction even
    # when the table is empty (no row to select) and the toolbar took focus
    focused = Signal()

    def __init__(self, headers):
        super().__init__(0, len(headers))
        # rowmeta[r] = ("sig", signal_index, addr) | ("free", addr)
        self.rowmeta: list[tuple] = []
        self.used_bytes = 0                    # trailing-free start (append addr)
        self._hl_row: int | None = None       # current drop-target highlight
        self._anim: QVariantAnimation | None = None
        self._loading = False                  # guard itemChanged during render
        self._name_col = len(headers) - 1      # Name is the last column
        self._preview_rows: list[int] = []     # live 'where the new data lands'
        self.itemChanged.connect(self._on_item_changed)
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Shift/Ctrl
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)     # we highlight the whole target row
        self.setDragDropMode(QAbstractItemView.InternalMove)
        hh = self.horizontalHeader()
        for c in range(len(headers)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)

    def mousePressEvent(self, e):
        # Clicking empty space (below the rows) clears the selection, so a following
        # Add reliably means "outside any slot -> new slot" (PROFINET) rather than
        # acting on a stale selection.
        self.focused.emit()                    # remember which table the user is in
        if not self.indexAt(e.position().toPoint()).isValid():
            self.clearSelection()
        super().mousePressEvent(e)

    def focusInEvent(self, e):
        self.focused.emit()
        super().focusInEvent(e)

    def _is_sig(self, row: int) -> bool:
        return 0 <= row < len(self.rowmeta) and self.rowmeta[row][0] == "sig"

    def selected_signal_table_rows(self) -> list[int]:
        """Selected *table* rows that are signals (for the drag image), sorted."""
        rows = {ix.row() for ix in self.selectionModel().selectedRows()}
        return sorted(r for r in rows if self._is_sig(r))

    def selected_signal_indices(self) -> list[int]:
        """Model signal indices of the selected signal rows, in row order."""
        return [self.rowmeta[r][1] for r in self.selected_signal_table_rows()]

    def rows_for_signals(self, sidx) -> list[int]:
        """Table rows currently showing the given signal indices."""
        want = set(sidx)
        return [r for r, m in enumerate(self.rowmeta)
                if m[0] == "sig" and m[1] in want]

    # -- inline (in-cell) rename --
    def _on_item_changed(self, item):
        """A name cell was edited in place -> tell the window to commit it."""
        if self._loading:
            return
        r, c = item.row(), item.column()
        if c != self._name_col:
            return
        if self._is_sig(r):
            self.nameEdited.emit(self.rowmeta[r][1], item.text())
        elif 0 <= r < len(self.rowmeta) and self.rowmeta[r] \
                and self.rowmeta[r][0] == "pnsig":          # PROFINET in-cell rename
            self.pnNameEdited.emit(self.rowmeta[r], item.text())

    def edit_signal_name(self, sig_index: int):
        """Open the in-cell editor on a signal's Name (F2 / double-click)."""
        rows = self.rows_for_signals([sig_index])
        if not rows:
            return
        it = self.item(rows[0], self._name_col)
        if it is not None:
            self.setCurrentItem(it)
            self.editItem(it)

    def _row_bg(self, row: int, brush: QBrush):
        # suppress itemChanged: a background change (drag highlight / flash) must
        # NOT be mistaken for an in-cell rename (that fired nameEdited -> a mid-drag
        # _refresh that rebuilt the table and broke drag-and-drop).
        prev = self._loading
        self._loading = True
        for col in range(self.columnCount()):
            it = self.item(row, col)
            if it:
                it.setBackground(brush)
        self._loading = prev

    # -- drop-target highlight --
    def _set_drop_highlight(self, row: int):
        if row == self._hl_row:
            return
        self._clear_drop_highlight()
        if 0 <= row < self.rowCount():
            c = QColor(theme.ACCENT)
            c.setAlpha(70)
            self._row_bg(row, QBrush(c))
            self._hl_row = row

    def _clear_drop_highlight(self):
        if self._hl_row is not None and self._hl_row < self.rowCount():
            self._row_bg(self._hl_row, QBrush())
        self._hl_row = None

    # -- live 'where will the new data land' preview (Add dialog) --
    def preview_range(self, start_byte: int, n_bytes: int, ok: bool = True):
        """Tint the rows covering bytes [start, start+n) so the user sees where the
        signals being added will go — accent (green) if it fits, red if it overlaps
        or runs past the interface size."""
        self.clear_preview()
        if n_bytes <= 0:
            return
        c = QColor(theme.ACCENT if ok else theme.ERR)
        c.setAlpha(70 if ok else 90)
        for r, m in enumerate(self.rowmeta):
            byte = m[-1] if m else None
            if isinstance(byte, int) and start_byte <= byte < start_byte + n_bytes:
                self._row_bg(r, QBrush(c))
                self._preview_rows.append(r)
        if not self._preview_rows and self.rowCount():
            self.scrollToBottom()                # appended past the end -> show it

    def selected_start_byte(self):
        """Byte address of the first selected row (signal OR free), or None — the
        start byte the Add form should default to."""
        rows = sorted({ix.row() for ix in self.selectionModel().selectedRows()})
        for r in rows:
            if 0 <= r < len(self.rowmeta) and self.rowmeta[r]:
                b = self.rowmeta[r][-1]
                if isinstance(b, int):
                    return b
        return None

    def pn_context(self):
        """PROFINET: the rowmeta tuple of the first selected slot/signal/free row, or
        None if nothing inside a slot is selected (clicked empty space below the bands).
        Drives the context-aware Add: None -> add a slot, otherwise -> add a signal to
        that slot. Tuple kinds: ('pnmod',slot,None) | ('pnsig',slot,relbyte,…,global) |
        ('pnfree',slot,relbyte,global)."""
        rows = sorted({ix.row() for ix in self.selectionModel().selectedRows()})
        for r in rows:
            if 0 <= r < len(self.rowmeta):
                m = self.rowmeta[r]
                if m and m[0] in ("pnmod", "pnsig", "pnfree"):
                    return m
        return None

    def pn_selected_metas(self):
        """All selected PROFINET rowmeta tuples (slot/signal/free), in row order — for
        multi-select delete."""
        rows = sorted({ix.row() for ix in self.selectionModel().selectedRows()})
        return [self.rowmeta[r] for r in rows
                if 0 <= r < len(self.rowmeta) and self.rowmeta[r]
                and self.rowmeta[r][0] in ("pnmod", "pnsig", "pnfree")]

    def clear_preview(self):
        for r in self._preview_rows:
            if r < self.rowCount():
                self._row_bg(r, QBrush())
        self._preview_rows = []

    # -- re-select + landing animation after a drop --
    def select_rows(self, rows: list[int]):
        self.clearSelection()
        if not rows:
            return
        sel = QItemSelection()
        last = self.columnCount() - 1
        for r in rows:
            sel.select(self.model().index(r, 0), self.model().index(r, last))
        self.selectionModel().select(
            sel, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.scrollToItem(self.item(rows[0], 0))
        self.setFocus()

    def flash_rows(self, rows: list[int]):
        """Brief accent fade on freshly dropped rows -> 'they land here'."""
        rows = [r for r in rows if r < self.rowCount()]
        if not rows:
            return
        anim = QVariantAnimation(self)
        start, end = QColor(theme.ACCENT), QColor(theme.ACCENT)
        start.setAlpha(170)
        end.setAlpha(0)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setDuration(480)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(
            lambda c: [self._row_bg(r, QBrush(c)) for r in rows])
        anim.finished.connect(lambda: [self._row_bg(r, QBrush()) for r in rows])
        anim.start()
        self._anim = anim   # keep a reference so it isn't garbage-collected

    def _target_row(self, pos) -> int:
        return self.indexAt(pos).row()

    # -- drag & drop --
    def _drag_pixmap(self, rows: list[int]) -> QPixmap:
        """A translucent stacked image of the dragged rows, so the drop
        target underneath stays visible while dragging."""
        last = self.columnCount() - 1
        width = self.viewport().width()
        rects = []
        for r in rows:
            left = self.visualRect(self.model().index(r, 0))
            right = self.visualRect(self.model().index(r, last))
            rects.append((r, left.united(right)))
        total_h = sum(rc.height() for _, rc in rects) or 1
        dpr = self.viewport().devicePixelRatioF()
        pix = QPixmap(int(width * dpr), int(total_h * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setOpacity(0.45)                       # translucent ghost
        y = 0
        for r, rc in rects:
            row = self.viewport().grab(QRect(0, rc.y(), width, rc.height()))
            p.drawPixmap(0, y, row)
            y += rc.height()
        p.end()
        return pix

    def _pn_drag_info(self):
        """What the current PROFINET selection drags: ('module', slot, rows) if a band
        header is selected (move the whole slot), else ('signals', [metas], rows) for the
        selected signal rows, else None. `rows` are the table rows for the ghost image."""
        sel = sorted({ix.row() for ix in self.selectionModel().selectedRows()})
        metas = [(r, self.rowmeta[r]) for r in sel
                 if 0 <= r < len(self.rowmeta) and self.rowmeta[r]]
        mod = next((m for _r, m in metas if m[0] == "pnmod"), None)
        if mod is not None:
            slot = mod[1]
            rows = [r for r, m in enumerate(self.rowmeta)
                    if m and len(m) > 1 and m[1] == slot
                    and m[0] in ("pnmod", "pnsig", "pnfree")]
            return ("module", slot, rows)
        sigs = [(r, m) for r, m in metas if m[0] == "pnsig"]
        if sigs:
            return ("signals", [m for _r, m in sigs], [r for r, _m in sigs])
        return None

    def startDrag(self, supported_actions):
        if getattr(self, "pn_mode", False):
            info = self._pn_drag_info()
            rows = info[2] if info else []
        else:
            rows = self.selected_signal_table_rows()
        if not rows:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText("fbconfig-rows")           # internal marker; we read selection
        drag.setMimeData(mime)
        pix = self._drag_pixmap(rows)
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(16, 10))         # offset so the cursor isn't covered
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, e):
        e.accept() if e.source() is self else e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is not self:
            e.ignore()
            return
        # let the base class run its drag auto-scroll (keeps scrolling while the cursor
        # is held near the top/bottom edge); the drop indicator is off, so it's invisible.
        super().dragMoveEvent(e)
        self._set_drop_highlight(self._target_row(e.position().toPoint()))
        e.accept()

    def dragLeaveEvent(self, e):
        self._clear_drop_highlight()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._clear_drop_highlight()
        if e.source() is not self:
            e.ignore()
            return
        row = self._target_row(e.position().toPoint())
        if getattr(self, "pn_mode", False):
            info = self._pn_drag_info()
            tgt = self.rowmeta[row] if 0 <= row < len(self.rowmeta) else None
            if not info or not tgt or tgt[0] not in ("pnfree", "pnsig", "pnmod"):
                e.ignore()
                return
            e.setDropAction(Qt.IgnoreAction)
            e.accept()
            if info[0] == "module":                # reorder whole modules
                if tgt[1] != info[1]:
                    self.pnModuleDropped.emit(info[1], tgt[1])
            else:                                   # move signal(s), UID travels
                self.pnRowsDropped.emit(info[1], tgt)
            return
        indices = self.selected_signal_indices()
        if not indices:
            e.ignore()
            return
        # Target byte address + whether the target is a free (empty) row, plus the
        # target signal index (for bit-granular reorder). Dropping onto free space
        # leaves the vacated location empty (POWERLINK); onto a signal reorders.
        n = sum(1 for m in self.rowmeta if m[0] == "sig")
        if 0 <= row < len(self.rowmeta):
            meta = self.rowmeta[row]
            target_addr = meta[-1]
            onto_free = meta[0] == "free"
            target_sig = meta[1] if meta[0] == "sig" else n
        else:
            target_addr = self.used_bytes      # past the end -> append into free
            onto_free = True
            target_sig = n
        e.setDropAction(Qt.IgnoreAction)   # we move via the model, not the view
        e.accept()
        self.rowsDropped.emit(indices, target_addr, onto_free, target_sig)


class IfaceTable(QWidget):
    """One direction: capacity bar + signal table. Read model -> render."""

    HEADERS = ["#", "Address", "Bits", "Type", "Elem", "UID", "Name"]

    def __init__(self, direction: str):
        super().__init__()
        self.direction = direction
        self._collapsed = set()        # PROFINET slots collapsed (signals hidden)
        self._r_iface = None           # last render args, for re-render on collapse toggle
        self._r_modules = None
        self._r_bit = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel(direction)
        self.title.setObjectName("H2")
        head.addWidget(self.title)
        head.addStretch()
        self.bar = QProgressBar()                   # In (and the only bar for non-PN)
        self.bar.setFixedWidth(260)
        head.addWidget(self.bar)
        self.bar2 = QProgressBar()                  # Out (PROFINET combined view only)
        self.bar2.setFixedWidth(260)
        self.bar2.setVisible(False)
        head.addWidget(self.bar2)
        lay.addLayout(head)

        self.table = SignalTable(self.HEADERS)
        self.table.cellClicked.connect(self._on_cell_clicked)
        lay.addWidget(self.table)

    def _on_cell_clicked(self, row, col):
        """Click the ▾/▸ toggle in column 0 of a slot header -> collapse/expand the slot
        (hide/show its signals) for a cleaner overview."""
        meta = self.table.rowmeta[row] if row < len(self.table.rowmeta) else None
        if col == 0 and meta and meta[0] == "pnmod":
            slot = meta[1]
            self._collapsed ^= {slot}
            if self._r_modules is not None:
                self.render(self._r_iface, self._r_bit, self._r_modules)

    def render(self, iface, bit_addressed=False, modules=None):
        self._r_iface, self._r_bit, self._r_modules = iface, bit_addressed, modules
        mx = iface.max_bytes
        row_slots = None
        if modules is not None:
            rows, rowmeta, used, row_slots, mx = self._rows_modules(modules)
        elif bit_addressed:
            rows, rowmeta, used = self._rows_bits(iface)
        else:
            rows, rowmeta, used = self._rows_bytes(iface)
        free = mx - used
        self.bar2.setVisible(False)            # only the PROFINET combined view shows Out
        self.title.setText(f"{iface.direction}   ·   {len(iface.signals)} signals")
        self.bar.setMaximum(max(1, mx))
        self.bar.setValue(used)
        self.bar.setFormat(f"{used}/{mx} B · {free} free")
        col = theme.usage_color(used, mx)
        self.bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {col}; border-radius:5px; }}")

        self.table.rowmeta = rowmeta
        self.table.pn_mode = modules is not None   # PROFINET drag = UID-preserving move
        self.table.used_bytes = used
        self.table._loading = True             # suppress itemChanged while filling
        self.table.setRowCount(len(rows))
        name_col = len(self.HEADERS) - 1
        for r, (num, addr, bits, typ, elem, uid, name, is_free, full_uid) \
                in enumerate(rows):
            for c, val in enumerate((num, addr, bits, typ, elem, uid, name)):
                it = QTableWidgetItem(val)
                if c in (0, 1, 4):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if full_uid:
                    # full systemTag (UUID) on hover -> the link key that
                    # travels with the signal when it is moved/shifted
                    it.setToolTip(f"systemTag (UID): {full_uid}")
                if is_free:
                    it.setForeground(QColor(theme.FREE_ROW))
                    it.setFlags(it.flags() & ~Qt.ItemIsDragEnabled)
                elif c == 3:
                    it.setForeground(QColor(theme.ACCENT))
                elif c == 5:
                    it.setForeground(QColor(theme.TEXT_DIM))
                if c == name_col and not is_free:
                    it.setFlags(it.flags() | Qt.ItemIsEditable)   # in-cell rename
                if row_slots is not None and row_slots[r] is not None:
                    slot, is_hdr, sdir = row_slots[r]
                    # input slots tinted teal, output slots amber — so In/Out are obvious
                    # in the single combined table; signal rows use a faint version.
                    base = theme.ACCENT if sdir == "input" else theme.WARN
                    tint = QColor(base)
                    tint.setAlpha(95 if is_hdr else 30)
                    it.setBackground(QBrush(tint))
                    if is_hdr:                       # band header: not editable, draggable
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table._loading = False

    def _rows_bytes(self, iface):
        """Byte-granular layout (POWERLINK): signals + per-byte free/gap rows.
        POWERLINK can also carry SINGLE sub-byte bits (arrayElements=1, size 0) —
        these are shown at their byte.bit address (a bit counter advances within a
        byte; pad_before starts a new byte)."""
        rows, rowmeta, cur, bib = [], [], 0, 0
        for idx, sig in enumerate(iface.signals):
            pad = sig.pad_before
            if bib > 0 and pad > 0:
                # leaving a (partly) bit-filled byte: the first pad unit just closes
                # that byte (already shown as bit rows) — it is NOT a free byte.
                cur += 1
                bib = 0
                pad -= 1
            for b in range(pad):                      # genuine reserved/free bytes
                rows.append(("", str(cur + b), "", "(free)", "", "", "reserved byte",
                             True, ""))
                rowmeta.append(("free", cur + b))
            off = cur + pad
            dt = by_sycon(sig.sycon_dtype)
            sz = sig.size
            sub_bit = dt.key == "bit" and sig.array_elements % 8 != 0
            if sub_bit:                               # single sub-byte bit
                addr = f"{off}.{bib}"
                bits = str(sig.array_elements)
            elif dt.key == "bit":                     # whole byte of 8 bit flags
                addr = str(off)
                bits = f"0-{sig.array_elements - 1}"
            else:
                addr = str(off) if sz == 1 else f"{off}-{off + sz - 1}"
                bits = ""
            rows.append((str(idx), addr, bits, dt.key, str(sig.array_elements),
                         sig.systemtag[:8], sig.name, False, sig.systemtag))
            rowmeta.append(("sig", idx, off))
            if sub_bit:
                bib += sig.array_elements
            cur = off + sz
        end = cur + (1 if bib > 0 else 0)              # an open bit-byte is used
        for b in range(iface.max_bytes - end):          # trailing free
            rows.append(("", str(end + b), "", "(free)", "", "", "unconfigured byte",
                         True, ""))
            rowmeta.append(("free", end + b))
        return rows, rowmeta, iface.used_bytes

    _DTB = {"bit": 1, "byte": 8, "signed8": 8, "unsigned8": 8,
            "word": 16, "signed16": 16, "unsigned16": 16,
            "dword": 32, "signed32": 32, "unsigned32": 32, "real32": 32}

    def _rows_modules(self, modules):
        """PROFINET module view: each module (slot) is a coloured band header followed by
        its bytes — signals placed within + free bytes (POWERLINK-style), addresses
        global and continuous. Signals never cross a slot boundary."""
        from collections import defaultdict
        rows, rowmeta, row_slots, total, used = [], [], [], 0, 0
        # combined view: all modules sorted by SLOT number (In + Out in one sequence so
        # the order is visible and drag-reorder is unambiguous).
        for mod in sorted(modules, key=lambda m: m["slot"]):
            slot, gs, size = mod["slot"], mod["global_start"], mod["size"]
            direction = mod["direction"]
            total = max(total, gs + size)
            mtype = en_label(mod["module_type"])       # display in English
            collapsed = slot in self._collapsed
            toggle = "▸" if collapsed else "▾"         # click to expand/collapse
            n = len(mod["signals"])
            extra = f"  ({n} sig)" if collapsed else ""
            rows.append((toggle, f"Slot {slot}", "", mtype, f"{size}B", "",
                         f"◢ Slot {slot} · {mtype}{extra}", False, ""))
            rowmeta.append(("pnmod", slot, None))
            row_slots.append((slot, True, direction))
            spans, bitbytes = {}, defaultdict(list)
            for s in mod["signals"]:
                w = self._DTB[s["dtype"]] * s["arr"]
                if s["dtype"] == "bit" and s["arr"] < 8:        # single/few bits in a byte
                    bitbytes[s["byte"]].append(s)
                else:
                    spans[s["byte"]] = (s, max(1, (w + 7) // 8))
            b = 0
            while b < size:                            # walk always (counts `used`);
                ga = gs + b                            # append rows only when expanded
                if b in spans:
                    s, nb = spans[b]
                    addr = str(ga) if nb == 1 else f"{ga}-{ga + nb - 1}"
                    uid = s.get("uid") or ""
                    if not collapsed:
                        rows.append(("", addr, "", s["dtype"], str(s["arr"]), uid[:8],
                                     s["name"], False, uid))
                        rowmeta.append(("pnsig", slot, b, ga))
                        row_slots.append((slot, False, direction))
                    used += nb
                    b += nb
                elif b in bitbytes:
                    if not collapsed:
                        for s in sorted(bitbytes[b], key=lambda x: x["bit"]):
                            uid = s.get("uid") or ""
                            rows.append(("", f"{ga}.{s['bit']}", str(s["bit"]), "bit",
                                         str(s["arr"]), uid[:8], s["name"], False, uid))
                            rowmeta.append(("pnsig", slot, b, s["bit"], ga))
                            row_slots.append((slot, False, direction))
                    used += 1
                    b += 1
                else:
                    if not collapsed:
                        rows.append(("", str(ga), "", "(free)", "", "", "free byte",
                                     True, ""))
                        rowmeta.append(("pnfree", slot, b, ga))
                        row_slots.append((slot, False, direction))
                    b += 1
        return rows, rowmeta, used, row_slots, max(1, total)

    def _rows_bits(self, iface):
        """Bit-granular layout (EtherNet/IP, EtherCAT, PROFINET): address as
        byte.bit. Unmapped whole bytes BETWEEN signals (gaps left by a delete) and
        after the last signal are shown as free rows, so a deleted byte appears as
        an empty slot in place — the other signals keep their addresses."""
        rows, rowmeta, cur = [], [], 0
        for idx, sig in enumerate(iface.signals):
            bo = sig.bit_offset or 0
            sb = bo // 8
            while cur < sb:                              # gap before this signal
                rows.append(("", str(cur), "", "(free)", "", "", "free byte (gap)",
                             True, ""))
                rowmeta.append(("free", cur))
                cur += 1
            dt = by_sycon(sig.sycon_dtype)
            if dt.key == "bit" and sig.array_elements % 8:
                addr = f"{sb}.{bo % 8}"                  # single / sub-byte bit
                bits = str(sig.array_elements)
                nby = 1
            else:
                nby = max(1, sig.bits // 8)
                addr = str(sb) if nby == 1 else f"{sb}-{sb + nby - 1}"
                bits = str(sig.bits)
            rows.append((str(idx), addr, bits, dt.key, str(sig.array_elements),
                         sig.systemtag[:8], sig.name, False, sig.systemtag))
            rowmeta.append(("sig", idx, sb))
            cur = max(cur, sb + nby)
        while cur < iface.max_bytes:                     # trailing free bytes
            rows.append(("", str(cur), "", "(free)", "", "", "unconfigured byte",
                         True, ""))
            rowmeta.append(("free", cur))
            cur += 1
        used = (sum(s.bits for s in iface.signals) + 7) // 8
        return rows, rowmeta, used


# ------------------------------------------------------------------ main window
class MainWindow(QMainWindow):
    def __init__(self, initial_folder: str | None = None):
        super().__init__()
        self.state = AppState()
        self.cfg = settings.load()
        import fbconfig as _fb
        self.setWindowTitle(_fb.APP_NAME)
        self.setWindowIcon(QIcon(asset("inasoft_icon.png")))
        self.setMinimumSize(1000, 620)     # never so small the toolbar overflows
        self.resize(1500, 900)             # open comfortably large (About visible)
        self.toasts = ToastManager(self)   # non-modal feedback notifications
        self._build_toolbar()
        self._build_body()
        self.overlay = Overlay(self)       # dimmed in-window card for About / Safety
        self.setStatusBar(self.statusBar())
        dark_titlebar(self)                # Windows draws the title bar -> make it dark
        self._refresh()
        if initial_folder:
            self._load_folder(initial_folder)
        else:
            self._restore_last()

    # ---- UI construction ----
    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, needs_model=True):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            if needs_model:
                self._model_actions.append(a)
            return a

        self._model_actions: list[QAction] = []
        self.open_action = act("📂  Open folder", self.on_open, "Ctrl+O",
                               needs_model=False)
        self.newcfg_action = QAction("➕  New config", self)
        self.newcfg_action.setToolTip("Add a fieldbus configuration to a robot "
                                      "that has none, by cloning a template.")
        self.newcfg_action.triggered.connect(self.on_new_config)
        tb.addAction(self.newcfg_action)
        tb.addSeparator()
        self.act_add = act("＋  Add", self.on_add, "Ctrl+N")
        self.act_rename = act("✎  Rename", self.on_rename, "F2")   # Edit — left of Delete
        self.act_delete = act("🗑  Delete", self.on_delete, "Del")
        self.act_resize = act("↔  Resize", self.on_resize)
        self.act_general = act("⚙  General", self.on_general)
        tb.addSeparator()
        self.uid_action = QAction("🔗  UIDs", self, checkable=True)
        self.uid_action.setChecked(True)
        self.uid_action.setToolTip("Show the systemTag (UID) column — the link "
                                   "key that travels with each signal when moved.")
        self.uid_action.toggled.connect(self._toggle_uids)
        tb.addAction(self.uid_action)
        self.safety_action = QAction("🛡  Safety", self)
        self.safety_action.triggered.connect(self.on_safety_switch)
        tb.addAction(self.safety_action)
        tb.addSeparator()
        self.save_action = act("💾  Save", self.on_save, "Ctrl+S")
        # push the About action to the far right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.about_action = QAction("ℹ  About", self)
        self.about_action.triggered.connect(self._show_about)
        tb.addAction(self.about_action)
        # Ctrl+P command palette — discoverable from the toolbar overflow too
        self.palette_action = QAction("⌘  Command palette", self)
        self.palette_action.setShortcut(QKeySequence("Ctrl+P"))
        self.palette_action.triggered.connect(self.open_palette)
        self.addAction(self.palette_action)   # window-level, no toolbar button

    def _build_body(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)             # [ main content | inspector dock ]
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        left = QWidget()
        outer.addWidget(left, 1)
        root = QVBoxLayout(left)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(14)

        # header strip — inasoft 3-stroke logo top-left, then the title
        strip = QHBoxLayout()
        logo = QLabel()
        pm = QPixmap(asset("inasoft_strokes.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaledToHeight(30, Qt.SmoothTransformation))
            strip.addWidget(logo)
            strip.addSpacing(14)
        import fbconfig as _fb
        title = QLabel(_fb.APP_NAME.upper())     # "FIELDBUS CONFIG EDITOR FOR STÄUBLI CS9"
        title.setObjectName("H1")
        strip.addWidget(title)
        strip.addStretch()
        self.badge = QLabel("no project")
        self.badge.setObjectName("Badge")
        strip.addWidget(self.badge)
        root.addLayout(strip)

        self.folder_lbl = QLabel("Folder: (none selected)")
        self.folder_lbl.setObjectName("Dim")
        root.addWidget(self.folder_lbl)

        # device + interface summary cards — kept in a splitter so their
        # divider lines up exactly with the In/Out tables below
        # (DEVICE over In, PROCESS IMAGE over Out).
        self.dev_card, self.dev_grid = _card("DEVICE")
        self.sum_card, self.sum_grid = _card("PROCESS IMAGE")
        self.cards_split = QSplitter(Qt.Horizontal)
        self.cards_split.addWidget(self.dev_card)
        self.cards_split.addWidget(self.sum_card)
        self.cards_split.setHandleWidth(26)
        self.cards_split.setChildrenCollapsible(False)
        self.cards_split.setSizes([590, 590])
        root.addWidget(self.cards_split)

        # signal tables
        self.tbl_split = QSplitter(Qt.Horizontal)
        self.in_tbl = IfaceTable("In")
        self.out_tbl = IfaceTable("Out")
        for t in (self.in_tbl, self.out_tbl):
            t.table.itemDoubleClicked.connect(self._on_table_dblclick)
        # only ONE table may hold a selection at a time
        self._sel_guard = False
        self.in_tbl.table.itemSelectionChanged.connect(
            lambda: self._exclusive_selection(self.in_tbl.table, self.out_tbl.table))
        self.out_tbl.table.itemSelectionChanged.connect(
            lambda: self._exclusive_selection(self.out_tbl.table, self.in_tbl.table))
        # clicking a row while the Add panel is open sets the start byte there
        self.in_tbl.table.itemSelectionChanged.connect(
            lambda: self._sync_add_start(self.in_tbl.table, "In"))
        self.out_tbl.table.itemSelectionChanged.connect(
            lambda: self._sync_add_start(self.out_tbl.table, "Out"))
        for t in (self.in_tbl, self.out_tbl):      # Edit/Delete enable by selection
            t.table.itemSelectionChanged.connect(self._update_action_states)
        self.in_tbl.table.rowsDropped.connect(
            lambda idxs, addr, free, tsig: self._place(
                self.state.model.inp, self.in_tbl, idxs, addr, free, tsig))
        self.out_tbl.table.rowsDropped.connect(
            lambda idxs, addr, free, tsig: self._place(
                self.state.model.out, self.out_tbl, idxs, addr, free, tsig))
        self.in_tbl.table.pnRowsDropped.connect(self._pn_move)
        self.out_tbl.table.pnRowsDropped.connect(self._pn_move)
        self.in_tbl.table.pnModuleDropped.connect(self._pn_move_module)
        self.out_tbl.table.pnModuleDropped.connect(self._pn_move_module)
        self._pn_last_dir = None
        self.in_tbl.table.nameEdited.connect(
            lambda i, n: self._commit_name(self.state.model.inp, i, n))
        self.out_tbl.table.nameEdited.connect(
            lambda i, n: self._commit_name(self.state.model.out, i, n))
        self.in_tbl.table.pnNameEdited.connect(self._pn_rename)
        self.out_tbl.table.pnNameEdited.connect(self._pn_rename)
        self.tbl_split.addWidget(self.in_tbl)
        self.tbl_split.addWidget(self.out_tbl)
        self.tbl_split.setHandleWidth(26)          # clear gap so the In scrollbar
        self.tbl_split.setChildrenCollapsible(False)   # isn't flush against Out
        self.tbl_split.setSizes([590, 590])
        root.addWidget(self.tbl_split, 1)

        # keep both dividers locked to the same x position
        self._syncing = False
        self.cards_split.splitterMoved.connect(
            lambda *_: self._sync_splits(self.cards_split, self.tbl_split))
        self.tbl_split.splitterMoved.connect(
            lambda *_: self._sync_splits(self.tbl_split, self.cards_split))

        hint = QLabel("Tip: Shift/Ctrl-click selects multiple signals · "
                      "drag a selection onto a row to reorder · "
                      "double-click a name to rename · Ctrl+P for commands.")
        hint.setObjectName("Dim")
        root.addWidget(hint)

        self.empty_lbl = QLabel(
            "Open a robot main folder (Ctrl+O) to load its fieldbus configuration.")
        self.empty_lbl.setObjectName("Dim")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_lbl)
        self._hint = hint

        # right-docked, non-modal inspector for Add / Resize / General forms
        self.inspector = InspectorPanel(self)
        self.inspector.applied.connect(self._on_inspector_applied)
        self.inspector.closed.connect(self._clear_previews)
        outer.addWidget(self.inspector, 0)

    def _sync_add_start(self, tbl, direction):
        """While the Add panel is open, clicking a row in the matching direction
        sets the start byte to that row — the user 'points' at where data lands."""
        form = self.inspector.current_form
        if not (self.inspector.isVisible() and isinstance(form, AddForm)):
            return
        if form.dir.currentText() != direction or form._single_bit():
            return
        b = tbl.selected_start_byte()
        if b is not None and b != form.startb.value():
            form.startb.setValue(b)            # triggers live re-highlight

    def _exclusive_selection(self, active, other):
        """When one table gains a selection, clear the other's — so only one
        direction is ever active (drives Add/Resize preselect)."""
        if self._sel_guard or not active.selectionModel().hasSelection():
            return
        if other.selectionModel().hasSelection():
            self._sel_guard = True
            other.clearSelection()
            self._sel_guard = False

    def _toggle_uids(self, on: bool):
        idx = IfaceTable.HEADERS.index("UID")
        for t in (self.in_tbl, self.out_tbl):
            t.table.setColumnHidden(idx, not on)

    def _sync_splits(self, src: QSplitter, dst: QSplitter):
        """Mirror one splitter's column split onto the other so the gap over
        the tables stays aligned with the gap over the cards."""
        if self._syncing:
            return
        self._syncing = True
        dst.setSizes(src.sizes())
        self._syncing = False

    # ---- grid helpers ----
    @staticmethod
    def _fill_grid(grid: QGridLayout, pairs):
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for r, (k, v, *rest) in enumerate(pairs):
            kl = QLabel(k)
            kl.setObjectName("Dim")
            vl = QLabel(str(v))
            vl.setObjectName("Mono")
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if rest:
                vl.setStyleSheet(f"color: {rest[0]};")
            grid.addWidget(kl, r, 0, Qt.AlignTop)
            grid.addWidget(vl, r, 1)
        grid.setColumnStretch(1, 1)

    # ---- refresh everything from state ----
    def _refresh(self):
        loaded = self.state.loaded
        bit = bool(loaded and self.state.model.raw.get("bit_addressed"))
        modular = bool(loaded and self.state.model.raw.get("modular"))
        # structural = add/delete · reorderable = drag-reorder · resizable = resize.
        # POWERLINK + EtherNet/IP + EtherCAT: all three. PROFINET: add/delete only
        # (module-aware), reorder/resize not yet. Safe variants (FSoE / PROFIsafe)
        # are editable too: the functional-safety data lives in SEPARATE files
        # (safety.pmt2 / safetyStruct.json) the writer never touches — editing the
        # standard process image leaves them byte-exact (validated).
        raw = self.state.model.raw if loaded else {}
        structural = bool(loaded and raw.get("structural", not modular))
        reorderable = bool(loaded and raw.get("reorderable", not modular))
        resizable = bool(loaded and raw.get("resizable", not modular))
        saf = raw.get("safety") if loaded else None
        is_pn = bool(loaded and raw.get("protocol_kind") == "profinet")
        # One context-aware Edit button for ALL protocols (add + edit by selection), so
        # the separate Add button is hidden everywhere (Ctrl+N still triggers add).
        self.act_add.setVisible(False)
        self.act_add.setEnabled(structural or is_pn)
        self.act_resize.setEnabled(resizable)
        gate_tip = ("" if structural else
                    "Not yet SyCon-safe for this protocol (binary device config is "
                    "not rebuilt) — use SyCon.net. POWERLINK supports full editing.")
        self.act_resize.setToolTip(gate_tip)
        self.act_general.setEnabled(loaded)        # IP / station / name — all protocols
        self.act_rename.setText("✎  Edit")
        self.act_rename.setToolTip(
            "Context: empty area → new slot · slot → edit slot · free byte → add "
            "signal · signal → edit signal." if is_pn
            else "Context: free byte → add signal · signal → rename · several signals "
                 "→ batch rename." if structural
            else "Rename the selected signal(s).")
        self.act_delete.setToolTip(
            "Select a signal to delete it, or an empty slot to remove the module."
            if is_pn else "Delete the selected signal(s).")
        # New config only when this robot has no fieldbus config yet.
        self.newcfg_action.setEnabled(not loaded)
        self.save_action.setEnabled(loaded)
        self._update_action_states()               # selection-dependent enable
        for t in (self.in_tbl, self.out_tbl):
            t.table.setDragEnabled(reorderable or is_pn)   # PN: UID-preserving signal move
            t.table.setAcceptDrops(reorderable or is_pn)
        has_safety = bool(saf and saf["tech"] in ("FSoE", "PROFIsafe"))
        self.safety_action.setVisible(has_safety)
        # always clickable when the protocol has safety — if a switch isn't
        # possible the handler explains why (better than a silent grey button).
        self.safety_action.setEnabled(has_safety)
        if has_safety:
            self.safety_action.setText(
                f"🛡  {saf['tech']}: {'ON' if saf['safe'] else 'OFF'}")
            self.safety_action.setToolTip(
                f"Click to switch {saf['tech']} "
                f"{'OFF' if saf['safe'] else 'ON'} (moves export sets + safety "
                "files, backup first). Needs both variants to exist."
                if saf.get("can_switch") else
                f"{saf['tech']} is {'ON' if saf['safe'] else 'OFF'}. To switch you "
                "need the other variant too (prepared in SyCon) — click for details.")
        self.folder_lbl.setText(
            f"Folder: {self.state.robot_dir}" if self.state.robot_dir
            else "Folder: (none selected)")

        self.dev_card.setVisible(loaded)
        self.sum_card.setVisible(loaded)
        self.in_tbl.setVisible(loaded)
        self.out_tbl.setVisible(loaded)
        self._hint.setVisible(loaded)
        self.empty_lbl.setVisible(not loaded)

        if not loaded:
            self.badge.setText("no project")
            if self.state.robot_dir:
                self.empty_lbl.setText(
                    "No fieldbus configuration in this robot.\n\n"
                    "Use  ➕ New config  to add one by cloning a template robot.")
            return

        m = self.state.model
        d = m.device
        node = f" · node {d.node_id}" if d.node_id is not None else ""
        # PROFINET has a byte-exact module/signal compiler, so it's fully editable even
        # though the flat `structural` gate (for the other modular protocols) is off.
        ro = ("  ·  modules + signals" if is_pn
              else "  ·  rename + save" if not structural else "")
        self.badge.setText(f"{d.base_name} · {d.protocol}{node}{ro}")

        dev_pairs = [
            ("Protocol", f"{d.protocol}  ({d.firmware})" if d.firmware else d.protocol),
        ]
        if d.node_id is not None:
            dev_pairs.append(("Node ID", d.node_id))
        dev_pairs += [
            ("Network name", d.node_name or "(none)"),
            ("Card IP", d.ip or "(unknown)"),
        ]
        if d.vendor_id is not None:
            dev_pairs.append(
                ("Vendor / Product",
                 f"0x{d.vendor_id:08x} / 0x{d.product_code:08x}"))
        nxd = m.raw.get("nxd")
        if nxd:
            ok = nxd["md5_ok"]
            dev_pairs.append((".nxd image",
                              f"{'MD5 OK' if ok else 'MD5 INVALID'} · {nxd['size']} bytes",
                              theme.OK if ok else theme.ERR))
        saf = m.raw.get("safety")
        if saf:
            on = saf["safe"]
            dev_pairs.append((
                "Safety",
                f"{saf['tech']}  {'ON' if on else 'OFF'}"
                + (f"  ·  {len(saf['files'])} safety file(s)" if saf["files"] else ""),
                theme.ACCENT if on else theme.TEXT_DIM))
        self._fill_grid(self.dev_grid, dev_pairs)

        sum_pairs = []
        for iface in m.interfaces():
            ts = iface.type_summary()
            tstr = ", ".join(f"{k}×{v}" for k, v in ts.items()) or "—"
            used = ((sum(s.bits for s in iface.signals) + 7) // 8 if bit
                    else iface.used_bytes)
            free = iface.max_bytes - used
            col = theme.usage_color(used, iface.max_bytes)
            sum_pairs.append(
                (iface.direction,
                 f"{used}/{iface.max_bytes} B · {free} free · "
                 f"{len(iface.signals)} sig", col))
            sum_pairs.append(("   types", tstr))
        if self.state.paths:
            p = self.state.paths
            sum_pairs.append(("exports",
                              f"[{'nxd' if p.nxd else '-'} | {'xml' if p.val3_xml else '-'}]"))
        self._fill_grid(self.sum_grid, sum_pairs)

        # PROFINET: ONE combined table with all modules (In + Out) sorted by slot number,
        # so the slot order is visible and drag-reorder is unambiguous. Other protocols
        # keep the two flat In/Out tables.
        if is_pn:
            pn_mods = m.raw.get("pn_module_list") or []
            self.out_tbl.setVisible(False)
            self.in_tbl.setVisible(True)
            self.in_tbl.render(m.inp, bit_addressed=bit, modules=pn_mods)
            iu, im = m.inp.used_bytes, m.inp.max_bytes
            ou, om = m.out.used_bytes, m.out.max_bytes
            self.in_tbl.title.setText("Modules")

            def _setbar(bar, used, mx, label):
                bar.setVisible(True)
                bar.setMaximum(max(1, mx))
                bar.setValue(used)
                bar.setFormat(f"{label} {used}/{mx} B · {mx - used} free")
                bar.setStyleSheet("QProgressBar::chunk { background: "
                                  f"{theme.usage_color(used, mx)}; border-radius:5px; }}")
            _setbar(self.in_tbl.bar, iu, im, "In")
            _setbar(self.in_tbl.bar2, ou, om, "Out")
        else:
            self.out_tbl.setVisible(True)
            self.in_tbl.render(m.inp, bit_addressed=bit, modules=None)
            self.out_tbl.render(m.out, bit_addressed=bit, modules=None)
        if is_pn:
            self._hint.setText(
                "PROFINET: one Edit button by selection — empty area → add slot · "
                "slot → edit (size/number) · free byte → add signal · signal → edit · "
                "several → batch rename. Drag a slot to reorder, a signal to move. "
                "Re-validate in SyCon.net before download. · Ctrl+P for commands.")
        elif not structural:
            self._hint.setText(
                f"{d.protocol}: rename + general + save only. Add / Delete / Resize "
                "are not yet SyCon-safe for this protocol — do them in SyCon.net. "
                "(POWERLINK supports full editing.) · Ctrl+P for commands.")
        elif bit:
            ops = ("rename · add · delete"
                   + (" · drag-to-reorder" if reorderable else "")
                   + (" · resize" if resizable else "") + " · save")
            tail = ("" if reorderable and resizable else
                    " Reorder / resize in SyCon.net." if not reorderable
                    else " Process-image size is fixed (resize in SyCon.net).")
            self._hint.setText(
                f"{d.protocol} (bit-granular): {ops}. "
                f"Re-validate structural changes in SyCon.net.{tail}")
        else:
            self._hint.setText(
                "Tip: Shift/Ctrl-click selects multiple signals · drag a selection "
                "onto a row to reorder · double-click a name to rename · Ctrl+P for commands.")

    def _status(self, msg: str, kind: str = "info"):
        """Status-bar text + a matching non-modal toast (info/success/warn/error)."""
        self.statusBar().showMessage(msg, 8000)
        self.toasts.show(msg, kind)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toasts.relayout()                 # keep toasts pinned bottom-right
        if self.overlay.isVisible():           # keep the dim backdrop full-window
            self.overlay.setGeometry(self.rect())

    def _show_about(self):
        self.overlay.show_card(AboutCard())

    # ---- command palette ----
    def _palette_commands(self):
        """(label, shortcut, QAction) for every action valid right now."""
        out = []
        for a in (self.open_action, self.newcfg_action, self.act_add,
                  self.act_delete, self.act_rename, self.act_resize,
                  self.act_general, self.uid_action, self.safety_action,
                  self.save_action, self.about_action):
            if a.isEnabled() and a.isVisible():
                label = " ".join(a.text().split())      # tidy the emoji spacing
                out.append((label, a.shortcut().toString(), a))
        return out

    def open_palette(self):
        cmds = self._palette_commands()
        if not cmds:
            return
        dlg = CommandPalette(cmds, self)
        g = self.geometry()
        dlg.move(g.center().x() - dlg.width() // 2, g.top() + 96)
        dlg.show()
        dlg.edit.setFocus()

    # ---- actions ----
    def on_open(self):
        # Start in the current project's folder if one is open this session, else the folder
        # remembered from the last session (settings 'last_folder') — not the .exe directory.
        start = self.state.robot_dir or self.cfg.get("last_folder", "") or ""
        if start and not Path(start).is_dir():
            start = ""
        folder = QFileDialog.getExistingDirectory(self, "Select the robot main folder", start)
        if folder:
            self._load_folder(folder)

    def _restore_last(self):
        """On startup, silently reopen the last configuration if still present."""
        folder = self.cfg.get("last_folder", "")
        if folder and Path(folder).is_dir():
            self._load_folder(folder, auto_spj=self.cfg.get("last_project", ""),
                              silent=True)

    def _load_folder(self, folder: str, auto_spj: str = "", silent: bool = False):
        self.inspector.close_form()              # drop any form bound to the old model
        self.state.robot_dir = folder
        try:
            projects = project.discover(folder)
        except Exception as e:
            self.state.paths = self.state.model = None
            if silent:
                self.state.robot_dir = None      # stale remembered folder -> clean
            self._refresh()                      # keep the folder selected (New config)
            if not silent:
                self._status(f"Could not scan this folder ({e}). Pick a robot main "
                             "folder, or use ➕ New config to add one.", "warn")
            return
        if not projects:
            self.state.paths, self.state.model = None, None
            if silent:                       # stale remembered folder -> start clean
                self.state.robot_dir = None
            self._refresh()
            if not silent:                   # config-less robot: offer New config
                self._status("No fieldbus configuration in this robot — use "
                             "➕ New config to add one by cloning a template.", "info")
            return
        paths = None
        if auto_spj:                          # reopen the previously chosen project
            paths = next((p for p in projects if str(p.spj) == auto_spj), None)
        if paths is None:
            # prefer the ACTIVE variant: its exports exist (the stashed/inactive
            # safety variant has no exports at the top level -> nxd/val3 are None).
            active = [p for p in projects if p.nxd or p.val3_xml]
            pick = active if active else projects
            if len(pick) > 1:
                dlg = ProjectPicker(pick, self)
                if dlg.exec() != dlg.DialogCode.Accepted:
                    return
                paths = pick[dlg.selected]
            else:
                paths = pick[0]
        try:
            self.state.model = project.load(paths)
        except Exception as e:
            self.state.paths = self.state.model = None
            if silent:
                self.state.robot_dir = None
            self._refresh()                      # keep the folder selected
            if not silent:
                self._status("This robot's fieldbus configuration could not be "
                             f"loaded ({e}) — it may be an unsupported variant.",
                             "warn")
            return
        self.state.paths = paths
        # remember for next launch
        self.cfg["last_folder"] = folder
        self.cfg["last_project"] = str(paths.spj)
        settings.save(self.cfg)
        d = self.state.model.device
        self._refresh()
        self._status(f"Loaded {d.base_name} ({d.protocol}) from {Path(folder).name}")

    def on_add(self):
        m = self.state.model
        # PROFINET has no Add button — the single context-aware Edit does add+edit.
        if m.raw.get("protocol_kind") == "profinet":
            return self._on_edit_profinet()
        in_free, out_free = m.inp.free_bytes, m.out.free_bytes
        if in_free <= 0 and out_free <= 0:           # nowhere to add -> toast, no panel
            self._status("In and Out are both full — delete a signal or resize "
                         "first.", "error")
            return
        # All flat protocols (EtherCAT / EtherNet/IP / POWERLINK) use the SAME page as
        # PROFINET (PnSignalForm) — start byte / type / separate-array / count / name /
        # numbering + live preview — so add+edit are identical everywhere.
        direction = self._active_direction() or "In"
        if direction == "In" and in_free <= 0:
            direction = "Out"
        elif direction == "Out" and out_free <= 0:
            direction = "In"
        iface = m.inp if direction == "In" else m.out
        tbl = self.in_tbl if direction == "In" else self.out_tbl
        start = tbl.table.selected_start_byte()
        if start is None:
            start = iface.used_bytes                  # default: the first free byte
        form = ec_signal_form(m, self.cfg, direction, start_byte=start)
        form.preview.connect(self._preview_add)
        self.inspector.open(form, "Add signal", "Add")

    def _pn_fail(self, title, exc):
        """Log a full diagnostic (traceback + blob state) for a failed PROFINET op and
        tell the user where the log is, so the real cause/origin is captured."""
        from fbconfig import pndiag
        from datetime import datetime
        paths = self.state.model.raw.get("paths") if self.state.loaded else None
        log = pndiag.log_failure(self.state.robot_dir, paths, title, exc, datetime.now())
        self._status(f"{title}: {exc}  ·  debug log: {log}", "error")

    def _update_action_states(self):
        """Enable Edit / Delete only when their action is possible for the current
        selection. Called on load and on every selection change."""
        if not self.state.loaded:
            for a in (self.act_rename, self.act_delete):
                a.setEnabled(False)
            return
        raw = self.state.model.raw
        if raw.get("protocol_kind") == "profinet":
            # Edit always works (empty selection -> add a slot in the active direction).
            self.act_rename.setEnabled(True)
            metas = (self.in_tbl.table.pn_selected_metas()
                     + self.out_tbl.table.pn_selected_metas())
            self.act_delete.setEnabled(any(m[0] in ("pnsig", "pnmod") for m in metas))
        else:
            has_sel = bool(self.in_tbl.table.selected_signal_indices()
                           or self.out_tbl.table.selected_signal_indices())
            structural = bool(raw.get("structural", not raw.get("modular")))
            # Edit works with a selection (rename) OR, if the protocol can add, on empty
            # space (add a signal). Delete needs a selection + an editable protocol.
            self.act_rename.setEnabled(has_sel or structural)
            self.act_delete.setEnabled(has_sel and structural)

    def _set_active_table(self, direction):
        """No-op for PROFINET now (single combined table). Kept for callers."""
        self._pn_last_dir = direction

    def _pn_module_by_slot(self, slot):
        """The PROFINET module dict for `slot` (slots are unique across In + Out)."""
        return next((x for x in (self.state.model.raw.get("pn_module_list") or [])
                     if x["slot"] == slot), None)

    def _preview_add(self, direction, start, n_bytes, fits=True):
        """Highlight where the signals being added will land (live) — green if it
        fits, red if it overlaps / runs past the interface size."""
        self._clear_previews()
        # PROFINET shows everything in the one combined table (in_tbl); other protocols
        # split In/Out.
        tbl = self.in_tbl if (self._is_pn() or direction == "In") else self.out_tbl
        tbl.table.preview_range(start, n_bytes, ok=fits)

    def _is_pn(self):
        return bool(self.state.loaded
                    and self.state.model.raw.get("protocol_kind") == "profinet")

    def _clear_previews(self):
        self.in_tbl.table.clear_preview()
        self.out_tbl.table.clear_preview()

    def _on_add_eip(self):
        m = self.state.model
        # Offer the FULL data-type catalog (not just types already present): every
        # bit-granular <Signal> block has the SAME structure regardless of dataType
        # (verified across real EtherCAT/EIP/PROFINET projects — they differ only in
        # dataType/arrayElements/accessPath/6100/6103, exactly the fields the writer
        # substitutes), so a new dtype is rendered byte-exact by cloning any block.
        form = EipAddForm(m, self.cfg, list(CATALOG.keys()), self._active_direction())
        self.inspector.open(form, f"Add signals ({m.device.protocol})", "Add")

    def on_delete(self):
        if self.state.loaded and self.state.model.raw.get("protocol_kind") == "profinet":
            return self._on_delete_profinet()
        iface, tbl, idxs = self._active_selection()
        if iface is None:
            self._status("Select one or more signals first.", "warn")
            return
        if len(idxs) == 1:
            msg = f"Delete '{iface.signals[idxs[0]].name}' from {iface.direction}?"
        else:
            msg = f"Delete {len(idxs)} signals from {iface.direction}?"
        detail = ("Following signals shift up to close the gap; the interface SIZE stays "
                  "the same (the freed bytes become free space at the end).")

        def _do():
            m = self.state.model
            for i in sorted(idxs, reverse=True):   # high -> low keeps indices valid
                iface.remove(i)
            # All flat protocols behave alike: close the gap, KEEP the interface size
            # (freed bytes -> trailing free). Bit-addressed repacks to bit offsets;
            # POWERLINK is byte-addressed (order already defines a contiguous layout).
            if m.raw.get("bit_addressed"):
                m.inp.repack_bits()
                m.out.repack_bits()
            m.raw["layout_dirty"] = True
            return f"Deleted {len(idxs)} signal(s) in {iface.direction}."

        self._overlay_confirm("Delete signals", msg, _do, detail,
                              confirm_text="Delete", danger=True)

    def on_rename(self):
        """The single context-aware Edit button (all protocols). PROFINET adds the slot
        contexts; the others: several signals -> batch rename · one signal -> in-cell
        rename · nothing/free byte -> add a signal (if the protocol can add)."""
        if not self.state.loaded:
            return
        raw = self.state.model.raw
        if raw.get("protocol_kind") == "profinet":
            return self._on_edit_profinet()
        iface, tbl, idxs = self._active_selection()
        if idxs and len(idxs) > 1:
            return self._rename_batch(iface, idxs)
        if idxs:
            # Structural-capable flat protocols (EtherCAT / POWERLINK) use the SAME full edit
            # page as PROFINET (start byte / type / separate-array / count / name / numbering
            # + live preview), UUID preserved. Rename-only protocols (EtherNet/IP) keep the
            # quick in-cell rename until their structural write exists.
            if bool(raw.get("structural", not raw.get("modular"))):
                form = ec_signal_form(self.state.model, self.cfg, iface.direction,
                                      edit_sig=iface.signals[idxs[0]])
                form.preview.connect(self._preview_add)
                return self.inspector.open(form, "Edit signal", "Apply")
            return tbl.table.edit_signal_name(idxs[0])     # in-cell rename (rename-only)
        # nothing (or a free byte) selected -> add, if this protocol supports it
        if bool(raw.get("structural", not raw.get("modular"))):
            return self.on_add()
        self._status("Select a signal to rename — Add/Delete need SyCon.net for this "
                     "protocol.", "warn")

    # ---------------------------------------------------- PROFINET delete / edit
    def _pn_resolve(self):
        """(direction, module_dict, ctx) for the current selection in the combined table.
        direction ('In'/'Out') comes from the selected module; both are None for an empty
        selection (-> add a new slot). ctx is the selected rowmeta tuple (or None)."""
        ctx = self.in_tbl.table.pn_context()
        if ctx is None:
            return None, None, None
        module = self._pn_module_by_slot(ctx[1])
        direction = ("In" if module["direction"] == "input" else "Out") if module else None
        return direction, module, ctx

    def _pn_find_signal(self, module, ctx):
        """The signal dict in `module` that row `ctx` points at (or None). A sub-byte
        single bit (arr<8) is a per-bit row ('pnsig',slot,b,bit,ga); everything else —
        values AND bit ARRAYS (arr>=8, whole bytes) — is a span row ('pnsig',slot,b,ga)."""
        if module is None or ctx[0] != "pnsig":
            return None
        rel_byte = ctx[2]
        bit = ctx[3] if len(ctx) == 5 else None
        for s in module["signals"]:
            if s["byte"] != rel_byte:
                continue
            single_bit = (s["dtype"] == "bit" and s.get("arr", 1) < 8)
            if bit is None and not single_bit:         # span row: value or bit-array
                return s
            if bit is not None and single_bit and s.get("bit", 0) == bit:
                return s
        return None

    def _pn_confirm(self, title, message, do, detail=None, confirm_text="Delete",
                    after=None):
        """Show a centered, dimmed-backdrop confirmation (like About) and, on confirm,
        run `do()` (which writes the files + returns a summary), then reload + refresh.
        `after` (optional) runs once after the refresh — e.g. to re-select a row."""
        card = ConfirmCard(title, message, detail, confirm_text=confirm_text, danger=True)

        def run():
            try:
                summary = do()
            except Exception as e:
                self._pn_fail(f"{title} failed", e)
                return
            if self.state.paths:
                try:
                    self.state.model = project.load(self.state.paths)
                except Exception as e:
                    self._status(f"Written but reload failed: {e}", "warn")
            self._refresh()
            if after is not None:
                after()
            if summary:
                self._status(summary, "success")
        card.confirmed.connect(run)
        self.overlay.show_card(card)

    def _on_delete_profinet(self):
        metas = self.in_tbl.table.pn_selected_metas()
        module_of = self._pn_module_by_slot

        # collect the selected signals (across modules); resolve each row to its dict
        targets = {}          # slot -> (module, [sig,...])
        for m in metas:
            if m[0] != "pnsig":
                continue
            mod = module_of(m[1])
            sig = self._pn_find_signal(mod, m)
            if mod and sig is not None:
                targets.setdefault(m[1], (mod, []))[1].append(sig)

        if targets:
            total = sum(len(v[1]) for v in targets.values())
            if total == 1:
                (mod, [sig]) = next(iter(targets.values()))
                title, message = "Delete signal", f"Delete signal '{sig['name']}'?"
            else:
                slots = ", ".join(str(s) for s in sorted(targets))
                title = "Delete signals"
                message = f"Delete {total} selected signals (slot {slots})?"

            def _do():
                from datetime import datetime
                from fbconfig import blob_pn, backup
                paths = self.state.model.raw["paths"]
                xml = paths.sycon_xml.read_text("utf-8", "replace")
                for slot, (mod, sigs) in targets.items():
                    remaining = [s for s in mod["signals"] if s not in sigs]
                    xml = blob_pn.write_module_signals(
                        xml, slot, remaining, mod["direction"], mod["global_start"])
                backup.make_backup(paths, datetime.now())
                paths.sycon_xml.write_text(xml, encoding="utf-8")
                return f"Deleted {total} signal(s)."

            # after deletion collapse the multi-selection to ONE row: the topmost byte
            # just freed (lowest slot, then byte, among the deleted signals).
            tslot, tbyte = min((slot, s["byte"])
                               for slot, (mod, sigs) in targets.items() for s in sigs)
            self._pn_confirm(title, message, _do,
                             "Frees the bytes inside the slot (no other signal moves).",
                             after=lambda: self._pn_select_slot_byte(tslot, tbyte))
            return

        # no signal selected -> an (empty) module band?
        mod_metas = [m for m in metas if m[0] == "pnmod"]
        if not mod_metas:
            self._status("Select a signal to delete, or an empty slot to remove it.",
                         "warn")
            return
        slot = mod_metas[0][1]
        module = module_of(slot)
        if module and module["signals"]:
            self._status(
                f"Slot {slot} still has {len(module['signals'])} signal(s) — delete "
                "those first; an empty slot is then removed.", "warn")
            return

        def _do_mod():
            from datetime import datetime
            from fbconfig import blob_pn, backup
            paths = self.state.model.raw["paths"]
            base_xml = paths.sycon_xml.read_text("utf-8", "replace")
            base_nxd = (paths.nxd.read_bytes()
                        if (paths.nxd and paths.nxd.is_file()) else None)
            new_xml, new_nxd = blob_pn.delete_catalog_module(base_xml, base_nxd, slot)
            backup.make_backup(paths, datetime.now())
            paths.sycon_xml.write_text(new_xml, encoding="utf-8")
            if new_nxd is not None and paths.nxd:
                paths.nxd.write_bytes(new_nxd)
            return f"Removed Slot {slot}."

        self._pn_confirm(
            "Remove slot", f"Remove the empty Slot {slot}?", _do_mod,
            "Deletes the module from the SyCon project + .nxd. Following modules keep "
            "their slot numbers. Re-open in SyCon.net to confirm.", confirm_text="Remove")

    def _on_edit_profinet(self):
        """The single context-aware PROFINET button (Edit): empty area -> add a new
        (empty) slot · slot header -> edit slot (size/number) · free byte -> add a
        signal there · existing signal -> edit it · several signals -> batch rename."""
        m = self.state.model
        sig_metas = [mm for mm in self.in_tbl.table.pn_selected_metas()
                     if mm[0] == "pnsig"]
        if len(sig_metas) > 1:                        # several signals -> batch rename
            return self._pn_batch_rename(sig_metas)
        direction, module, ctx = self._pn_resolve()
        if ctx is None:                               # empty area -> new slot (pick In/Out)
            self.inspector.open(ModuleForm(m), "Add slot", "Add")
            return
        if ctx[0] == "pnmod":                         # slot header -> edit slot
            if module is None:
                self._status("Could not resolve the selected slot — reload.", "warn")
                return
            self.inspector.open(ModuleEditForm(m, module),
                                f"Edit slot {module['slot']}", "Apply")
            return
        if module is None:
            self._status("Could not resolve the selected slot — reload.", "warn")
            return
        if ctx[0] == "pnfree":                        # free byte -> add a signal here
            rel = ctx[2] if (len(ctx) > 2 and isinstance(ctx[2], int)) else 0
            form = PnSignalForm(m, self.cfg, module, direction, start_byte=rel)
            form.preview.connect(self._preview_add)
            self.inspector.open(form, f"Add signal · Slot {module['slot']}", "Add")
            return
        if ctx[0] == "pnsig":                         # existing signal -> edit it
            sig = self._pn_find_signal(module, ctx)
            if sig is None:
                self._status("Could not resolve the selected signal.", "warn")
                return
            form = PnSignalForm(m, self.cfg, module, direction, edit=sig)
            form.preview.connect(self._preview_add)
            self.inspector.open(form, f"Edit signal · Slot {module['slot']}", "Apply")

    def _pn_move(self, src_metas, tgt_meta):
        """Drag-move PROFINET signal(s) onto the drop target (free byte / slot), KEEPING
        each signal's UID ([[systemtag-must-travel]]). Within a slot or across slots of
        the same direction; the placed range is fit- and overlap-checked against the
        target slot. Rewrites the affected modules' device XML, then reloads."""
        mods = self.state.model.raw.get("pn_module_list") or []
        bits = IfaceTable._DTB
        by_slot = {mod["slot"]: mod for mod in mods}

        def module_of(slot):
            return by_slot.get(slot)
        srcs = []
        for m in src_metas:
            mod = module_of(m[1])
            sig = self._pn_find_signal(mod, m)
            if mod and sig is not None:
                srcs.append((mod, sig))
        if not srcs:
            return
        tslot = tgt_meta[1]
        tmod = module_of(tslot)
        if tmod is None:
            return
        # In and Out are separate address spaces -> can't move a signal across directions
        if any(mod["direction"] != tmod["direction"] for mod, _ in srcs):
            self._status("Can't move a signal between Input and Output modules.", "warn")
            return
        direction = "In" if tmod["direction"] == "input" else "Out"
        tbyte = tgt_meta[2] if (len(tgt_meta) > 2 and isinstance(tgt_meta[2], int)) else 0
        insert = tgt_meta[0] in ("pnsig", "pnmod")   # drop ONTO a signal/header -> insert

        final = {mod["slot"]: [dict(s) for s in mod["signals"]] for mod in mods}
        move_uids = {s.get("uid") for _m, s in srcs}
        for mod, _sig in srcs:                     # take the sources out of their slots
            final[mod["slot"]] = [s for s in final[mod["slot"]]
                                  if s.get("uid") not in move_uids]
        moved = [dict(s) for _m, s in srcs]

        def pack(seq, start_byte):
            """Lay signals out contiguously (bit-granular) from start_byte; values are
            byte-aligned, bits packed tight. Returns the list with new byte/bit."""
            out, cur = [], start_byte * 8
            for s in seq:
                w = bits[s["dtype"]] * s.get("arr", 1)
                if s["dtype"] != "bit":
                    cur = ((cur + 7) // 8) * 8     # value signals start on a byte
                s = dict(s, byte=cur // 8, bit=(cur % 8 if s["dtype"] == "bit" else 0))
                out.append(s)
                cur += w
            return out

        if insert:
            # insert the moved signals BEFORE the target byte; the target + everything
            # below it shift down (re-packed), signals above keep their place.
            rest = sorted(final[tslot], key=lambda s: (s["byte"], s.get("bit", 0)))
            above = [s for s in rest if s["byte"] < tbyte]
            below = [s for s in rest if s["byte"] >= tbyte]
            final[tslot] = above + pack(moved + below, tbyte)
        else:
            final[tslot].extend(pack(moved, tbyte))   # drop on free space -> place there

        occ = set()                                # fit + overlap check on target slot
        for s in final[tslot]:
            w = bits[s["dtype"]] * s.get("arr", 1)
            base = s["byte"] * 8 + (s["bit"] if s["dtype"] == "bit" else 0)
            if base + w > tmod["size"] * 8:
                self._status(f"Does not fit in Slot {tslot} (past the slot end).",
                             "error")
                return
            rng = set(range(base, base + w))
            if occ & rng:
                self._status(f"Overlaps an existing signal in Slot {tslot}.", "error")
                return
            occ |= rng

        from datetime import datetime
        from fbconfig import blob_pn, backup
        paths = self.state.model.raw["paths"]
        try:
            xml = paths.sycon_xml.read_text("utf-8", "replace")
            for slot in {mod["slot"] for mod, _ in srcs} | {tslot}:
                mod = by_slot[slot]
                xml = blob_pn.write_module_signals(xml, slot, final[slot],
                                                   mod["direction"], mod["global_start"])
            backup.make_backup(paths, datetime.now())
            paths.sycon_xml.write_text(xml, encoding="utf-8")
        except Exception as e:
            self._pn_fail("Signal move failed", e)
            return
        if self.state.paths:
            try:
                self.state.model = project.load(self.state.paths)
            except Exception as e:
                self._status(f"Moved but reload failed: {e}", "warn")
        self._refresh()
        self._pn_select_uids(move_uids)              # keep the moved signals selected
        self._status(f"Moved {len(moved)} signal(s) to Slot {tslot}.", "success")

    def _pn_move_module(self, src_slot, tgt_slot):
        """Reorder whole modules: place the dragged module BEFORE the drop-target module
        (rebuild the set in the new order, slots renumbered 1..N, signal UIDs kept).
        Same-direction modules below shift in global address; the device stays valid."""
        from datetime import datetime
        from fbconfig import blob_pn, backup
        paths = self.state.model.raw["paths"]
        nxd = (paths.nxd.read_bytes() if (paths.nxd and paths.nxd.is_file()) else None)
        if nxd is None:
            self._status("No exported .nxd to rebuild — export once in SyCon first.",
                         "warn")
            return
        xml = paths.sycon_xml.read_text("utf-8", "replace")
        try:
            specs = blob_pn.capture_modules(xml)
        except Exception as e:
            self._pn_fail("Could not read modules", e)
            return
        si = next((i for i, s in enumerate(specs) if s["slot"] == src_slot), None)
        ti = next((i for i, s in enumerate(specs) if s["slot"] == tgt_slot), None)
        if si is None or ti is None:
            return
        spec = specs.pop(si)
        if si < ti:
            ti -= 1
        specs.insert(ti, spec)
        for i, s in enumerate(specs):              # renumber 1..N in the new order
            s["slot"] = i + 1
        try:
            new_xml, new_nxd = blob_pn.rebuild_modules(xml, nxd, specs)
        except Exception as e:
            self._pn_fail("Move failed", e)
            return
        backup.make_backup(paths, datetime.now())
        paths.sycon_xml.write_text(new_xml, encoding="utf-8")
        if new_nxd is not None and paths.nxd:
            paths.nxd.write_bytes(new_nxd)
        if self.state.paths:
            try:
                self.state.model = project.load(self.state.paths)
            except Exception as e:
                self._status(f"Moved but reload failed: {e}", "warn")
        self._refresh()
        self._pn_select_module(ti + 1)
        self._status(f"Moved module to slot {ti + 1} (slots renumbered to match order).",
                     "success")

    def _pn_select_module(self, slot):
        for r, m in enumerate(self.in_tbl.table.rowmeta):
            if m and m[0] == "pnmod" and m[1] == slot:
                self.in_tbl.table.select_rows([r])
                self.in_tbl.table.setFocus()
                return

    def _pn_select_slot_byte(self, slot, rel_byte):
        """Select the row for (slot, module-relative byte) in the combined table — used
        after a delete to land on the topmost freed byte."""
        for r, m in enumerate(self.in_tbl.table.rowmeta):
            if (m and m[0] in ("pnfree", "pnsig") and m[1] == slot
                    and len(m) > 2 and m[2] == rel_byte):
                self.in_tbl.table.select_rows([r])
                self.in_tbl.table.setFocus()
                return

    def _pn_rename(self, meta, new):
        """Commit a double-click in-cell rename of a PROFINET signal (device-XML only;
        the signal's UID is kept, like POWERLINK)."""
        new = new.strip()
        module = self._pn_module_by_slot(meta[1])
        sig = self._pn_find_signal(module, meta)
        if sig is None or not new or sig["name"] == new:
            self._refresh()                       # revert an empty / no-op edit
            return
        uid = sig.get("uid")
        sigs = [dict(s) for s in module["signals"]]
        for s in sigs:
            if s.get("uid") == uid:
                s["name"] = new
        from datetime import datetime
        from fbconfig import blob_pn, backup
        paths = self.state.model.raw["paths"]
        try:
            xml = paths.sycon_xml.read_text("utf-8", "replace")
            new_xml = blob_pn.write_module_signals(xml, module["slot"], sigs,
                                                   module["direction"],
                                                   module["global_start"])
            backup.make_backup(paths, datetime.now())
            paths.sycon_xml.write_text(new_xml, encoding="utf-8")
        except Exception as e:
            self._pn_fail("Rename failed", e)
            return
        if self.state.paths:
            try:
                self.state.model = project.load(self.state.paths)
            except Exception as e:
                self._status(f"Renamed but reload failed: {e}", "warn")
        self._refresh()
        self._pn_select_uids([uid])
        self._status(f"Renamed to '{new}'.", "success")

    def _pn_batch_rename(self, sig_metas):
        """Rename several selected PROFINET signals at once: a base name + numbering
        (BatchRenameDialog). Only the name changes — UID/type/address are kept."""
        from datetime import datetime
        from fbconfig import blob_pn, backup
        bym = {x["slot"]: x for x in (self.state.model.raw.get("pn_module_list") or [])}
        targets = []                                   # (slot, sig) in selection order
        for mm in sig_metas:
            module = bym.get(mm[1])
            sig = self._pn_find_signal(module, mm)
            if module is not None and sig is not None:
                targets.append((mm[1], sig))
        if len(targets) < 2:
            return
        dlg = BatchRenameDialog(len(targets), self.cfg, "", self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        scheme = dlg.scheme()
        newname = {sig.get("uid"): scheme.name(i) for i, (_s, sig) in enumerate(targets)}
        paths = self.state.model.raw["paths"]
        try:
            xml = paths.sycon_xml.read_text("utf-8", "replace")
            for slot in {s for s, _ in targets}:
                module = bym[slot]
                sigs = [dict(s, name=newname.get(s.get("uid"), s["name"]))
                        for s in module["signals"]]
                xml = blob_pn.write_module_signals(xml, slot, sigs, module["direction"],
                                                   module["global_start"])
            backup.make_backup(paths, datetime.now())
            paths.sycon_xml.write_text(xml, encoding="utf-8")
        except Exception as e:
            self._pn_fail("Batch rename failed", e)
            return
        if self.state.paths:
            try:
                self.state.model = project.load(self.state.paths)
            except Exception as e:
                self._status(f"Renamed but reload failed: {e}", "warn")
        self.cfg = settings.load()
        self._refresh()
        self._pn_select_uids(list(newname))
        self._status(f"Renamed {len(targets)} signals.", "success")

    def _pn_select_uids(self, uids):
        """Select exactly the combined-table rows of the signals with these UIDs (after an
        Add/Edit/Move, so only the affected signal rows stay selected). Matches on (slot,
        module-relative byte) since the global byte is ambiguous across In/Out."""
        uids = set(uids or [])
        want = {(m["slot"], s["byte"])
                for m in (self.state.model.raw.get("pn_module_list") or [])
                for s in m["signals"] if s.get("uid") in uids}
        rows = [r for r, m in enumerate(self.in_tbl.table.rowmeta)
                if m and m[0] == "pnsig" and len(m) > 2 and (m[1], m[2]) in want]
        if rows:
            self.in_tbl.table.select_rows(rows)
            self.in_tbl.table.setFocus()

    def _commit_name(self, iface, idx, new):
        """Apply an in-cell rename (from SignalTable.nameEdited)."""
        new = new.strip()
        if idx >= len(iface.signals):
            return
        if not new or iface.signals[idx].name == new:
            self._refresh()                       # revert an empty/no-op edit
            return
        iface.signals[idx].name = new
        self._refresh()
        self._status(f"Renamed to '{new}'.", "success")

    def _rename_batch(self, iface, idxs):
        dlg = BatchRenameDialog(len(idxs), self.cfg, iface.direction, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        scheme = dlg.scheme()
        for ordinal, i in enumerate(idxs):
            iface.signals[i].name = scheme.name(ordinal)
        self.cfg = settings.load()
        self._refresh()
        self._status(f"Renamed {len(idxs)} signals with prefix '{scheme.prefix}'.",
                     "success")

    def on_resize(self):
        raw = self.state.model.raw
        # PROFINET's image size = sum of its slot modules (no single size field), so
        # a size change means swapping catalog modules — still skeleton-based.
        if raw.get("protocol_kind") == "profinet":
            return self._on_resize_profinet()
        # EtherNet/IP OLD variant: size from a SyCon-saved skeleton (assembly sizes live
        # in the blob). The NETX 51 RE/EIS variant (eip_eis) carries the size in the .nxd
        # (@1141/@1181) -> simple spinbox like EtherCAT/POWERLINK.
        if raw.get("protocol_kind") == "ethernetip" and not raw.get("eip_eis"):
            return self._on_resize_eip()
        # POWERLINK (@324/@326), EtherCAT (@380/@408), EIP-EIS (.nxd @1141/@1181) carry the
        # image size in the .nxd, patched on save -> simple byte-count spinbox.
        form = ResizeForm(self.state.model, self._active_direction())
        self.inspector.open(form, "Change interface size", "Apply")

    def _on_resize_profinet(self):
        """PROFINET size change via skeleton: pick a SyCon PROFINET project of the
        TARGET module composition; its modules + .nxd are used and the current
        signals keep their byte positions. A fit-check rejects a skeleton whose
        module layout does not hold the existing signals (use one from THIS robot)."""
        from fbconfig import sycon
        m = self.state.model
        folder = QFileDialog.getExistingDirectory(
            self, "Select a SyCon PROFINET project of the TARGET size (skeleton)")
        if not folder:
            return
        skels = [p for p in project.discover(folder)
                 if (p.protocol or "") == "PROFINET" and p.nxd]
        if not skels:
            self._status("No PROFINET project (with .nxd) found in that folder.", "warn")
            return
        skel = skels[0]
        try:
            isz, osz = eip.pn_skeleton_sizes(skel)
            blob = sycon.blob_from_xml(sycon.read_xml(skel.sycon_xml))
            skranges = eip._pn_module_ranges(sycon.detail_block(blob)[2])
        except Exception as e:
            self._status(f"Could not read skeleton: {e}", "error")
            return

        def fits(sig, direction):
            bo = sig.bit_offset or 0
            b0, b1 = bo // 8, (bo + sig.bits - 1) // 8
            return any(d == direction and st <= b0 and b1 < st + sz
                       for d, st, sz in skranges)
        bad = ([s for s in m.inp.signals if not fits(s, "input")]
               + [s for s in m.out.signals if not fits(s, "output")])
        if bad:
            self._status(
                f"Skeleton's module layout doesn't hold {len(bad)} existing "
                "signal(s) — use a skeleton from THIS robot at the new size.", "warn")
            return
        m.inp.max_bytes, m.out.max_bytes = isz, osz
        m.raw["pn_skeleton"] = skel
        m.raw["pn_modules"] = skranges
        m.raw["layout_dirty"] = True
        self._refresh()
        self._status(f"Resized to In={isz} B / Out={osz} B via skeleton "
                     f"'{skel.spj.parent.name}'. Save to apply; re-validate in SyCon.net.",
                     "success")

    def _on_resize_eip(self):
        """EtherNet/IP size change via skeleton injection: the user picks a
        SyCon-saved EtherNet/IP project of the TARGET size; its size structure
        (OLE2/CIP/length/configMD5/.nxd) is reused and the current signals are
        injected. The skeleton's network identity (IP/node) is used, so it should
        be saved from THIS robot at the new size."""
        m = self.state.model
        folder = QFileDialog.getExistingDirectory(
            self, "Select a SyCon EtherNet/IP project of the TARGET size (skeleton)")
        if not folder:
            return
        skels = [p for p in project.discover(folder)
                 if "ethernet" in (p.protocol or "").lower()]
        if not skels:
            self._status("No EtherNet/IP project found in that folder.", "warn")
            return
        skel = skels[0]
        try:
            isz, osz = eip.skeleton_sizes(skel)
        except Exception as e:
            self._status(f"Could not read skeleton: {e}", "error")
            return
        old = (m.inp.max_bytes, m.out.max_bytes)
        m.inp.max_bytes, m.out.max_bytes = isz, osz
        try:
            m.inp.repack_bits()
            m.out.repack_bits()
        except ValueError as e:
            m.inp.max_bytes, m.out.max_bytes = old
            m.inp.repack_bits()
            m.out.repack_bits()
            self._status(f"{e} Delete signals to fit the smaller size first.", "warn")
            return
        m.raw["eip_skeleton"] = skel
        m.raw["layout_dirty"] = True
        self._refresh()
        self._status(f"Resized to In={isz} B / Out={osz} B via skeleton "
                     f"'{skel.spj.parent.name}'. Save to apply; re-validate in SyCon.net.",
                     "success")

    def on_general(self):
        # POWERLINK writes node/IP/name into the .nxd; the bit-granular protocols
        # (EtherNet/IP/EtherCAT/PROFINET) carry the identity in the Val3 station.
        if self.state.model.raw.get("bit_addressed"):
            form = StationForm(self.state.model)
        else:
            form = GeneralForm(self.state.model)
        self.inspector.open(form, "General data", "Apply")

    def _on_inspector_applied(self, summary: str):
        """A side-panel form applied successfully: re-render, flash any new rows
        (Add), close the panel, and confirm with a toast."""
        form = self.inspector.current_form
        added = getattr(form, "added", None)
        direction = getattr(form, "direction", None)
        # the PROFINET direction this form acted on — keep that table active afterwards
        # (closing the panel returns focus to a table, which would otherwise flip the
        # active direction back to In).
        self.cfg = settings.load()
        self.inspector.close_form()
        if getattr(form, "reload_after", False) and self.state.paths:
            # the form wrote the project files directly (module compiler) — re-read so
            # the new module + its signal show.
            try:
                self.state.model = project.load(self.state.paths)
            except Exception as e:
                self._status(f"Module written but reload failed: {e}", "warn")
        self._refresh()
        # PROFINET Add/Edit: re-select exactly the new/edited signal rows in the one
        # combined table.
        pn_uids = getattr(form, "pn_added_uids", None)
        if pn_uids:
            self._pn_select_uids(pn_uids)
        elif added and direction:
            iface = self.state.model.inp if direction == "In" else self.state.model.out
            tbl = self.in_tbl if direction == "In" else self.out_tbl
            rows = tbl.table.rows_for_signals(
                [iface.signals.index(s) for s in added if s in iface.signals])
            tbl.table.select_rows(rows)
            tbl.table.flash_rows(rows)
        if summary:
            self._status(summary, "success")

    def on_new_config(self):
        """Add a fieldbus configuration to a robot that has none: the user just
        picks the PROTOCOL and a minimal reference config of that protocol is
        created (from a bundled template). They then edit names / IP / signals."""
        target = self.state.robot_dir
        if not target:
            target = QFileDialog.getExistingDirectory(
                self, "Select the robot folder to add a configuration to")
            if not target:
                return
            self.state.robot_dir = target
        if project.discover(target):
            self._status("This robot already has a fieldbus configuration.", "warn")
            return
        dlg = NewConfigDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.protocol_key:
            return
        tmpl = asset(f"templates/{dlg.protocol_key}.zip")
        if not Path(tmpl).is_file():
            self._status(f"No bundled template for {dlg.protocol_name}.", "error")
            return
        try:
            project.add_config_from_template(target, tmpl)
        except Exception as e:
            self._status(f"New config failed: {e}", "error")
            return
        self._load_folder(target)
        self._status(f"Created a {dlg.protocol_name} configuration — edit the "
                     "signals, network name and IP, then save.", "success")

    def on_safety_switch(self):
        """Open the Safety manager: switch the variant (if both present), apply a
        saved template to bring in the other variant, or save the current variant
        as a template for reuse on other robots."""
        m = self.state.model
        if not (m and m.raw.get("safety")):
            return
        card = SafetyCard(self.state.robot_dir, m)
        card.changed.connect(self._on_safety_changed)
        self.overlay.show_card(card)

    def _on_safety_changed(self):
        self.overlay.close_card()
        self._load_folder(self.state.robot_dir)
        self._status("Safety variant changed · backup made · set IP/station in "
                     "General, then re-validate in SyCon.net.", "success")

    def on_save(self):
        m, paths = self.state.model, self.state.paths
        if paths is None:
            self._status("No project paths — cannot save.", "warn")
            return
        files = [paths.sycon_xml]
        if paths.val3_xml:
            files.append(paths.val3_xml)
        if paths.nxd:
            files.append(paths.nxd)
        detail = ("Writes:\n  " + "\n  ".join(Path(f).name for f in files) +
                  f"\n\nIn {m.inp.used_bytes}/{m.inp.max_bytes} B, "
                  f"Out {m.out.used_bytes}/{m.out.max_bytes} B.\n"
                  "A timestamped ZIP backup is created first.")

        def _do():
            try:
                res = savemod.save(m, paths)
            except Exception as e:
                raise ValueError(f"Save failed: {e}")
            self._show_save_result(res)        # green toast or overlay failure report
            return ""                          # result already reported

        self._overlay_confirm("Save to disk", "Save configuration to disk?", _do,
                              detail, confirm_text="Save")

    def _overlay_confirm(self, title, message, do, detail=None, confirm_text="OK",
                         danger=False):
        """Centered, dimmed inline-modal confirmation (like About). On confirm, runs
        do() DEFERRED (after this card has closed) so do() may itself open another
        overlay card (e.g. the save-failure report) without it being closed again."""
        from PySide6.QtCore import QTimer
        card = ConfirmCard(title, message, detail, confirm_text=confirm_text,
                           danger=danger)

        def deferred():
            try:
                summary = do()
            except ValueError as e:
                self._status(str(e), "error")
                return
            self._refresh()
            if summary:
                self._status(summary, "success")
        card.confirmed.connect(lambda: QTimer.singleShot(0, deferred))
        self.overlay.show_card(card)

    def _show_save_result(self, res):
        if res.verified:
            # success is non-blocking: a green toast, details in the status bar
            self._status(
                f"Saved · round-trip OK · backup {Path(res.backup).name}. "
                "Validate in SyCon.net before downloading to the robot.", "success")
            return
        # failure must be acknowledged, not glanced at — shown as a centered, dimmed
        # inline overlay card (like About / the delete confirm), not an OS pop-up.
        detail = (f"Backup: {Path(res.backup).name}\n"
                  + "\n".join(f"Wrote:  {Path(p).name}" for p in res.written)
                  + "\n\nProblems:\n" + "\n".join(f"  - {p}" for p in res.problems)
                  + "\n\nThe backup lets you restore. Do NOT use these files until checked.")
        card = ConfirmCard("Save result", "Round-trip self-check FAILED — the change is "
                           "UNVERIFIED.", detail, confirm_text="OK", danger=True,
                           cancel=False)
        self.overlay.show_card(card)
        self._status("Saved · round-trip FAILED — see report", "error")

    # ---- drag & drop placement ----
    def _place(self, iface, iface_tbl, indices, target_addr, onto_free=False,
               target_sig=0):
        """Drag-drop placement. Bit-granular protocols (EtherNet/IP) reorder by
        signal index and re-pack the bits; byte-granular ones place by address."""
        if self.state.model.raw.get("bit_addressed"):
            if self.state.model.raw.get("protocol_kind") == "profinet":
                self._relocate_profinet(iface, iface_tbl, indices, target_addr)
            else:
                self._reorder_eip(iface, iface_tbl, indices, target_sig)
            return
        indices = sorted(set(indices))
        if not indices:
            return
        moved = [iface.signals[i] for i in indices]
        try:
            iface.place_at(moved, target_addr, leave_gap=onto_free)
        except ValueError as e:
            self._status(str(e), "warn")
            return
        self._refresh()
        new_sidx = [iface.signals.index(s) for s in moved]
        rows = iface_tbl.table.rows_for_signals(new_sidx)
        iface_tbl.table.select_rows(rows)
        iface_tbl.table.flash_rows(rows)
        gap = moved[0].pad_before
        extra = f" (gap {gap} B before)" if gap else ""
        self._status(f"Moved {len(moved)} signal(s) in {iface.direction} "
                     f"to address {target_addr}{extra}.", "success")

    def _reorder_eip(self, iface, iface_tbl, indices, target):
        """Bit-granular reorder (EtherNet/IP): move the block before signal index
        `target`, then re-pack bit offsets (bits tight, multi-byte byte-aligned)."""
        indices = sorted(set(indices))
        if not indices:
            return
        moved = [iface.signals[i] for i in indices]
        remaining = [s for i, s in enumerate(iface.signals) if i not in indices]
        insert_at = target - sum(1 for i in indices if i < target)
        iface.signals[:] = remaining[:insert_at] + moved + remaining[insert_at:]
        try:
            iface.repack_bits()
            self.state.model.raw["layout_dirty"] = True
        except ValueError as e:                  # shouldn't happen on a pure move
            self._status(str(e), "warn")
        self._refresh()
        rows = iface_tbl.table.rows_for_signals(
            [iface.signals.index(s) for s in moved])
        iface_tbl.table.select_rows(rows)
        iface_tbl.table.flash_rows(rows)
        self._status(f"Reordered {len(moved)} signal(s) in {iface.direction}. "
                     "Re-validate in SyCon.net before download.", "success")

    def _relocate_profinet(self, iface, iface_tbl, indices, target_addr):
        """PROFINET reorder = relocate the dragged signal(s) to a free slot at/after
        the drop byte, within a module (no repack; fixed module layout)."""
        indices = sorted(set(indices))
        if not indices:
            return
        moved = [iface.signals[i] for i in indices]
        direction = "input" if iface.direction == "In" else "output"
        try:
            eip.pn_relocate(self.state.model, direction, moved, target_addr)
        except ValueError as e:
            self._status(str(e), "warn")
            return
        self.state.model.raw["layout_dirty"] = True
        self._refresh()
        rows = iface_tbl.table.rows_for_signals([iface.signals.index(s) for s in moved])
        iface_tbl.table.select_rows(rows)
        iface_tbl.table.flash_rows(rows)
        self._status(f"Moved {len(moved)} signal(s) to byte {target_addr} in "
                     f"{iface.direction}. Re-validate in SyCon.net.", "success")

    # ---- table helpers ----
    def _active_selection(self):
        """(iface, table, [indices]) for the table with a signal selection.

        Prefers the focused table so Delete/Rename act where the user is.
        """
        pairs = ((self.in_tbl, self.state.model.inp),
                 (self.out_tbl, self.state.model.out))
        for tbl, iface in pairs:
            if tbl.table.hasFocus():
                idxs = tbl.table.selected_signal_indices()
                if idxs:
                    return iface, tbl, idxs
        for tbl, iface in pairs:
            idxs = tbl.table.selected_signal_indices()
            if idxs:
                return iface, tbl, idxs
        return None, None, None

    def _active_direction(self):
        """Direction of the table the user is working in: prefer a selection,
        then keyboard focus. Used to preselect In/Out in the Add dialog."""
        pairs = ((self.in_tbl, self.state.model.inp),
                 (self.out_tbl, self.state.model.out))
        for tbl, iface in pairs:
            if tbl.table.selected_signal_indices():
                return iface.direction
        for tbl, iface in pairs:
            if tbl.table.hasFocus():
                return iface.direction
        return None

    def _on_table_dblclick(self, item):
        for tbl, iface in ((self.in_tbl, self.state.model.inp),
                           (self.out_tbl, self.state.model.out)):
            if item.tableWidget() is tbl.table:
                meta = tbl.table.rowmeta[item.row()]
                if meta[0] == "sig":
                    tbl.table.edit_signal_name(meta[1])   # in-cell editor
                elif meta[0] == "pnsig":                  # PROFINET: edit the Name cell
                    it = tbl.table.item(item.row(), tbl.table._name_col)
                    if it is not None:
                        tbl.table.setCurrentItem(it)
                        tbl.table.editItem(it)
                return
