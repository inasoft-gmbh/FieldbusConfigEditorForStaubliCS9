"""netX DBM (.NXD) parser/serializer for the PROFINET main.nxd — the foundation of
the Stufe-2 module compiler.

The file is a nested tag database (see memory nxd-dbm-format):
  • header[0:144]: magic '.NXD', @72 u32 file size, @84 16-byte MD5 (over data[136:]),
    @140 u32 record count, @144 N×u64 record offsets.
  • each record is a sequence of items: TAGS [u32 type][u32 total_len_incl_8B][content]
    (type 0x19 record header, 0x1f row directory, 0x17 data row, 0x16 name trailer,
    0x15 column schema) interleaved with RAW gaps (the ASCII record name etc.).

Many u32 fields are POINTERS = the absolute file offset of another item (the 0x19
header points to its dir/trailer/name; a 0x1f directory lists its rows' offsets).
parse() finds every pointer by matching a u32 to an item start; serialize() lays the
model out and rewrites every pointer to its target's new position, then the record
directory, file size and MD5. Verified by byte-exact round-trip on docs/nxd_samples."""
from __future__ import annotations
import hashlib
import struct

DATA_OFF = 136
MD5_OFF = 0x54          # 84
SIZE_OFF = 0x48         # 72
COUNT_OFF = 140
DIR_OFF = 144
_TAGS = (0x15, 0x16, 0x17, 0x19, 0x1e, 0x1f)


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


class Item:
    """A tag (type in _TAGS) or a raw gap (type=None). `content` excludes the 8-byte
    [type][len] header for tags; for a raw gap it is the whole blob."""
    __slots__ = ("type", "content", "pos")

    def __init__(self, type_, content):
        self.type = type_
        self.content = bytearray(content)
        self.pos = 0

    def is_tag(self):
        return self.type is not None

    def length(self):
        return (8 + len(self.content)) if self.is_tag() else len(self.content)

    def to_bytes(self):
        if self.is_tag():
            return struct.pack("<II", self.type, 8 + len(self.content)) + bytes(self.content)
        return bytes(self.content)


class Nxd:
    def __init__(self, head, records, pointers):
        self.head = bytearray(head)
        self.records = records          # list[list[Item]]
        self.pointers = pointers        # list[(src_item, content_offset, target_item)]


def parse(d: bytes) -> Nxd:
    count = _u32(d, COUNT_OFF)
    offs = [struct.unpack_from("<Q", d, DIR_OFF + 8 * i)[0] for i in range(count)]
    ends = offs[1:] + [len(d)]
    records = []
    posmap = {}
    for lo, hi in zip(offs, ends):
        items = []
        i = lo
        while i < hi:
            t = _u32(d, i) if i + 8 <= hi else None
            ln = _u32(d, i + 4) if i + 8 <= hi else 0
            if t in _TAGS and 8 <= ln <= hi - i:
                it = Item(t, d[i + 8:i + ln])
                it.pos = i
                items.append(it)
                posmap[i] = it
                i += ln
            else:                                   # raw gap until the next valid tag
                j = i + 4
                while j < hi:
                    tt = _u32(d, j) if j + 8 <= hi else None
                    ll = _u32(d, j + 4) if j + 8 <= hi else 0
                    if tt in _TAGS and 8 <= ll <= hi - j:
                        break
                    j += 4
                it = Item(None, d[i:j])
                it.pos = i
                items.append(it)
                posmap[i] = it
                i = j
        records.append(items)
    # pointer discovery: any 4-aligned u32 in a tag's content equal to an item start
    pointers = []
    for items in records:
        for it in items:
            if not it.is_tag():
                continue
            for off in range(0, len(it.content) - 3, 4):
                v = struct.unpack_from("<I", it.content, off)[0]
                tgt = posmap.get(v)
                if tgt is not None and v != it.pos:
                    pointers.append((it, off, tgt))
    return Nxd(d[:DIR_OFF + 8 * count], records, pointers)


def serialize(nxd: Nxd) -> bytes:
    count = len(nxd.records)
    head = bytearray(nxd.head)
    struct.pack_into("<I", head, COUNT_OFF, count)
    dir_end = DIR_OFF + 8 * count
    if len(head) < dir_end:
        head += b"\x00" * (dir_end - len(head))
    # pass 1: assign positions
    pos = dir_end
    rec_offsets = []
    for items in nxd.records:
        rec_offsets.append(pos)
        for it in items:
            it.pos = pos
            pos += it.length()
    # pass 2: patch pointers to targets' new positions
    for src, off, tgt in nxd.pointers:
        struct.pack_into("<I", src.content, off, tgt.pos)
    # assemble
    out = bytearray(head)
    for i, o in enumerate(rec_offsets):
        struct.pack_into("<Q", out, DIR_OFF + 8 * i, o)
    for items in nxd.records:
        for it in items:
            out += it.to_bytes()
    struct.pack_into("<I", out, SIZE_OFF, len(out))
    out[MD5_OFF:MD5_OFF + 16] = hashlib.md5(bytes(out[DATA_OFF:])).digest()
    return bytes(out)


# ---- module compiler (add a user module) -------------------------------------
# Record indices (fixed order; see memory nxd-dbm-format).
R_SUBMODULES, R_MODULES, R_MODULES_IO, R_PNIOD_ID, R_SIGNALS, R_CHANNEL = 2, 3, 5, 6, 7, 11
_IO_TAIL = bytes.fromhex("0000000000ffffffff00000000000000")   # MODULES_IO const tail
_SIG_DATATYPE = 0x1c


def _rows(items):
    return [it for it in items if it.type == 0x17]


def _poke(rec, rel, val, nxd=None):
    """Write a u32 DATA value at a record-relative byte offset (counting tag headers). If
    `nxd` is given, drop any pointer the parser may have inferred at that spot — a data
    value (e.g. a CHANNEL DPM-area size) can coincidentally equal a tag-start offset and
    be mis-detected as a pointer, which serialize() would then relocate. Without this,
    re-parsing a nxd that already has such a value (the add_catalog_module chain) corrupts
    it by +212 per added module."""
    pos = 0
    for it in rec:
        L = it.length()
        if pos <= rel < pos + L:
            co = rel - pos - (8 if it.is_tag() else 0)
            if nxd is not None:
                nxd.pointers = [(s, o, t) for (s, o, t) in nxd.pointers
                                if not (s is it and o == co)]
            struct.pack_into("<I", it.content, co, val)
            return
        pos += L


def _insert_before_trailer(items, new_items):
    for k, it in enumerate(items):
        if it.type == 0x16:
            items[k:k] = new_items
            return
    items.extend(new_items)


def add_module(nxd: Nxd, slot: int, module_ident: int, submodule_ident: int,
               datalen: int, direction: str):
    """Append one PROFINET user module (one signal) to all 5 module tables, exactly as
    SyCon's compiler does. `direction` = 'input' (Eingang, device produces) or 'output'
    (Ausgang, device consumes)."""
    inlen = datalen if direction == "input" else 0
    outlen = datalen if direction == "output" else 0
    sigdir = 0 if direction == "input" else 1

    # MODULES: row [ncols=2][rowid][slot u16][ident u32][rowid u16][0]; 1f count+1, +offset
    rec = nxd.records[R_MODULES]
    rowid = len(_rows(rec)) + 1
    row = Item(0x17, struct.pack("<IIHIHI", 2, rowid, slot, module_ident, rowid, 0))
    _insert_before_trailer(rec, [row])
    f = next(it for it in rec if it.type == 0x1f)
    struct.pack_into("<I", f.content, 4, struct.unpack_from("<I", f.content, 4)[0] + 1)
    off = len(f.content)
    f.content += b"\x00\x00\x00\x00"
    nxd.pointers.append((f, off, row))

    # These three keep a per-row pointer LIST in the 0x19 header (grows +4/row) plus
    # the row-count at content+4; rows themselves are contiguous before the 0x16.
    def add_simple(idx, row_content):
        rec = nxd.records[idx]
        row = Item(0x17, row_content)
        _insert_before_trailer(rec, [row])
        h = next(it for it in rec if it.type == 0x19)
        struct.pack_into("<I", h.content, 4, struct.unpack_from("<I", h.content, 4)[0] + 1)
        o = len(h.content)
        h.content += b"\x00\x00\x00\x00"
        nxd.pointers.append((h, o, row))

    # MODULES_IO: row [3][rowid][rowid]+tail; the tail carries the module's DPM byte
    # OFFSET (u32 @content+13) = cumulative bytes of the modules of the SAME direction
    # added before it (proven: 8+64 Byte inputs -> 2nd module offset 8). Sum the existing
    # same-direction SIGNALS rows (this module's SIGNALS row isn't added yet).
    sigdir_v = 0 if direction == "input" else 1
    dpm_off = sum(struct.unpack_from("<I", it.content, 20)[0]
                  for it in nxd.records[R_SIGNALS]
                  if it.type == 0x17 and struct.unpack_from("<I", it.content, 16)[0] == _SIG_DATATYPE
                  and struct.unpack_from("<I", it.content, 24)[0] == sigdir_v)
    io_tail = bytearray(_IO_TAIL)
    struct.pack_into("<I", io_tail, 1, dpm_off)
    rowid = len(_rows(nxd.records[R_MODULES_IO])) + 1
    add_simple(R_MODULES_IO, struct.pack("<III", 3, rowid, rowid) + bytes(io_tail))
    # PNIOD_MODULE_ID: row [0][rowid][slot u16][0001][rowid]
    rowid = len(_rows(nxd.records[R_PNIOD_ID])) + 1
    add_simple(R_PNIOD_ID, struct.pack("<IIHHI", 0, rowid, slot, 1, rowid))
    # SIGNALS: row [0][rowid×3][datatype][datalen][direction]
    rowid = len(_rows(nxd.records[R_SIGNALS])) + 1
    add_simple(R_SIGNALS, struct.pack("<IIIIIII", 0, rowid, rowid, rowid,
                                      _SIG_DATATYPE, datalen, sigdir))

    # SUBMODULES: new (1f group + row); 0x19 group-count+1 + a pointer to the new 1f
    rec = nxd.records[R_SUBMODULES]
    groupidx = sum(1 for it in rec if it.type == 0x1f) + 1
    subrow = Item(0x17, struct.pack("<IIHHHHH", 2, 1, 1, submodule_ident, 0, inlen, outlen)
                  + b"\x00" * 18)
    subf = Item(0x1f, struct.pack("<III", groupidx, 2, 0) + b"\x00\x00\x00\x00")
    nxd.pointers.append((subf, 12, subrow))
    _insert_before_trailer(rec, [subf, subrow])
    h = next(it for it in rec if it.type == 0x19)
    struct.pack_into("<I", h.content, 4, struct.unpack_from("<I", h.content, 4)[0] + 1)
    off = len(h.content)
    h.content += b"\x00\x00\x00\x00"
    nxd.pointers.append((h, off, subf))

    # CHANNEL_SETTING: per-direction DPM-area size = 0x800 * max(2, TOTAL bytes of that
    # direction) — proven: 8+64 Byte inputs -> 0x800*72 = 0x24000 (a single field per
    # direction, NOT per module). Recompute from the whole SIGNALS table each call so
    # several modules of the same direction accumulate. Offsets: input @76&88, out @80&84.
    total = {0: 0, 1: 0}
    for it in nxd.records[R_SIGNALS]:
        if it.type == 0x17 and struct.unpack_from("<I", it.content, 16)[0] == _SIG_DATATYPE:
            total[struct.unpack_from("<I", it.content, 24)[0]] += \
                struct.unpack_from("<I", it.content, 20)[0]
    def area(t):                                   # total rounded UP to even (61->62, 35->36)
        return 0x800 * max(2, t + (t & 1))
    if total[0]:
        for o in (76, 88):
            _poke(nxd.records[R_CHANNEL], o, area(total[0]), nxd)
    if total[1]:
        for o in (80, 84):
            _poke(nxd.records[R_CHANNEL], o, area(total[1]), nxd)


def _rebuild_tail(nxd, header, targets, rowtype):
    """Rebuild a record header's row-pointer TAIL (the contiguous 4-byte slots add()
    appends — one per row/group, after the header's structural pointers): drop the old
    slots + their pointer entries, re-append one slot per remaining `targets` item in
    order, and DECREMENT the running counter at content+4 (the exact inverse of the +1
    add() applies — it is rows+1, not a plain row count, so set-to-len would be wrong)."""
    entries = [o for (s, o, t) in nxd.pointers if s is header and t.type == rowtype]
    tail_start = min(entries) if entries else len(header.content)
    nxd.pointers = [(s, o, t) for (s, o, t) in nxd.pointers
                    if not (s is header and t.type == rowtype)]
    del header.content[tail_start:]
    for t in targets:
        o = len(header.content)
        header.content += b"\x00\x00\x00\x00"
        nxd.pointers.append((header, o, t))
    struct.pack_into("<I", header.content, 4,
                     struct.unpack_from("<I", header.content, 4)[0] - 1)


def delete_module(nxd: Nxd, slot: int):
    """Remove the module in `slot` from all 5 module tables (inverse of add_module): drop
    its row/group, rebuild each header's pointer tail, renumber the remaining rows 1..K,
    recompute the MODULES_IO DPM offsets + CHANNEL totals. A round-trip (add then delete)
    reproduces the original byte-exact (tests/test_regression)."""
    mrows = _rows(nxd.records[R_MODULES])
    k = next((i for i, it in enumerate(mrows)
              if struct.unpack_from("<H", it.content, 8)[0] == slot), None)
    if k is None:
        raise ValueError(f"slot {slot} not present in the .nxd MODULES table")

    # MODULES (0x1f directory): drop k-th row, rebuild tail, renumber rowids (@4 u32, @14 u16)
    rec = nxd.records[R_MODULES]
    rec.remove(_rows(rec)[k])
    f = next(it for it in rec if it.type == 0x1f)
    rows = _rows(rec)
    _rebuild_tail(nxd, f, rows, 0x17)
    for i, it in enumerate(rows):
        struct.pack_into("<I", it.content, 4, i + 1)
        struct.pack_into("<H", it.content, 14, i + 1)

    # MODULES_IO / PNIOD_ID / SIGNALS (0x19 header): drop k-th row, rebuild tail
    def del_simple(idx):
        rec = nxd.records[idx]
        rec.remove(_rows(rec)[k])
        h = next(it for it in rec if it.type == 0x19)
        rows = _rows(rec)
        _rebuild_tail(nxd, h, rows, 0x17)
        return rows
    io_rows = del_simple(R_MODULES_IO)
    for i, it in enumerate(io_rows):                    # rowid @4 & @8
        struct.pack_into("<I", it.content, 4, i + 1)
        struct.pack_into("<I", it.content, 8, i + 1)
    pid_rows = del_simple(R_PNIOD_ID)
    for i, it in enumerate(pid_rows):                   # rowid @4 & @12
        struct.pack_into("<I", it.content, 4, i + 1)
        struct.pack_into("<I", it.content, 12, i + 1)
    sig_rows = del_simple(R_SIGNALS)
    for i, it in enumerate(sig_rows):                   # rowid @4 & @8 & @12
        struct.pack_into("<I", it.content, 4, i + 1)
        struct.pack_into("<I", it.content, 8, i + 1)
        struct.pack_into("<I", it.content, 12, i + 1)

    # SUBMODULES (0x19 -> 0x1f groups -> subrow): drop the k-th group (1f + its row)
    rec = nxd.records[R_SUBMODULES]
    groups = [it for it in rec if it.type == 0x1f]
    dead_f = groups[k]
    gi = rec.index(dead_f)
    dead_row = next(it for it in rec[gi + 1:] if it.type == 0x17)
    rec.remove(dead_f)
    rec.remove(dead_row)
    h = next(it for it in rec if it.type == 0x19)
    rgroups = [it for it in rec if it.type == 0x1f]
    _rebuild_tail(nxd, h, rgroups, 0x1f)
    for i, it in enumerate(rgroups):                    # groupidx @0
        struct.pack_into("<I", it.content, 0, i + 1)

    # MODULES_IO DPM offset (u32 @content+13) = cumulative SAME-direction bytes before;
    # direction + datalen come from the matching SIGNALS row (same module order).
    cum = {0: 0, 1: 0}
    for io, sg in zip(io_rows, sig_rows):
        datalen = struct.unpack_from("<I", sg.content, 20)[0]
        d = struct.unpack_from("<I", sg.content, 24)[0]
        struct.pack_into("<I", io.content, 13, cum[d])
        cum[d] += datalen

    # CHANNEL_SETTING: recompute per-direction DPM area; a direction with 0 bytes resets
    # to the 0-module default (0x0), NOT area(0) — verified against sample 00.
    total = {0: 0, 1: 0}
    for sg in sig_rows:
        if struct.unpack_from("<I", sg.content, 16)[0] == _SIG_DATATYPE:
            total[struct.unpack_from("<I", sg.content, 24)[0]] += \
                struct.unpack_from("<I", sg.content, 20)[0]

    def chan(t):
        return 0 if t == 0 else 0x800 * max(2, t + (t & 1))
    for o in (76, 88):
        _poke(nxd.records[R_CHANNEL], o, chan(total[0]), nxd)
    for o in (80, 84):
        _poke(nxd.records[R_CHANNEL], o, chan(total[1]), nxd)

    # drop any pointer whose source/target item was removed (keeps serialize() clean)
    live = {id(it) for rec in nxd.records for it in rec}
    nxd.pointers = [p for p in nxd.pointers if id(p[0]) in live and id(p[2]) in live]
