"""Minimal MS-CFB (OLE2 compound file) WRITER — builds a valid compound file from a
storage/stream tree. olefile is read-only; SyCon's PROFINET blob needs new nested
storages+streams (STLModuleMap/...), so we rebuild the whole CFB from a {name: child}
tree (child = bytes for a stream, dict for a storage).

CFB v3: 512-byte sectors, 64-byte mini-sectors, 4096 mini cutoff. Directory entries
are a per-storage red-black tree keyed by (utf-16 length, uppercased utf-16). Readers
(olefile, Windows StructuredStorage) traverse left/right/child as a BST; we emit a
balanced BST (all-black) which they accept. Verified by byte round-trip via olefile:
parse a real blob's streams, rebuild, and read every stream back identically."""
from __future__ import annotations
import struct

SECTOR = 512
MINISECTOR = 64
MINICUTOFF = 4096
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF


class _Entry:
    def __init__(self, name, etype):
        self.name = name              # str
        self.etype = etype            # 1 storage, 2 stream, 5 root
        self.color = 1                # black
        self.left = NOSTREAM
        self.right = NOSTREAM
        self.child = NOSTREAM
        self.clsid = b"\x00" * 16
        self.start = ENDOFCHAIN
        self.size = 0
        self.data = b""               # stream payload
        self.sid = -1


def _name_key(name):
    u = name.encode("utf-16-le")
    return (len(u) + 2, name.upper().encode("utf-16-le"))


def _build_tree(sid_of):
    """Given a list of child entries, sort by CFB key and return the root sid of a
    balanced BST, linking left/right on the entries."""
    def build(items):
        if not items:
            return NOSTREAM
        mid = len(items) // 2
        node = items[mid]
        node.left = build(items[:mid])
        node.right = build(items[mid + 1:])
        return node.sid
    return build


def read_tree(ole) -> dict:
    """Full storage/stream tree of an open olefile.OleFileIO, INCLUDING empty storages
    (olefile.listdir() returns only streams; SyCon's blob has empty storages like
    STLRecordParamDataMap / BitDescVec that must be preserved or modules vanish)."""
    def rec(entry, path):
        node = {}
        for kid in entry.kids:
            p = path + [kid.name]
            node[kid.name] = rec(kid, p) if kid.entry_type == 1 else ole.openstream(p).read()
        return node
    return rec(ole.root, [])


def build(tree: dict) -> bytes:
    """tree: nested dict; value bytes => stream, dict => storage. Returns CFB bytes."""
    entries = []

    def add(name, etype):
        e = _Entry(name, etype)
        e.sid = len(entries)
        entries.append(e)
        return e

    root = add("Root Entry", 5)

    def walk(node_dict, parent):
        children = []
        for nm, val in node_dict.items():
            if isinstance(val, dict):
                e = add(nm, 1)
                walk(val, e)
            else:
                e = add(nm, 2)
                e.data = bytes(val)
                e.size = len(e.data)
            children.append(e)
        # link children as a balanced BST sorted by CFB key
        children.sort(key=lambda e: _name_key(e.name))
        builder = _build_tree(None)
        parent.child = builder(children)

    walk(tree, root)

    # ---- allocate storage: split mini vs regular streams ----
    mini_stream = bytearray()
    minifat = []
    fat = []
    # regular-sector payloads to place, in order; we assemble after sizing
    # 1) mini stream: pack all stream entries with size < cutoff (size>0)
    for e in entries:
        if e.etype == 2 and 0 < e.size < MINICUTOFF:
            e.start = len(mini_stream) // MINISECTOR
            chunk = e.data + b"\x00" * (-len(e.data) % MINISECTOR)
            n = len(chunk) // MINISECTOR
            base = len(minifat)
            for k in range(n):
                minifat.append(base + k + 1 if k < n - 1 else ENDOFCHAIN)
            mini_stream += chunk
    root.size = len(mini_stream)
    # pad minifat to sector multiple
    while len(minifat) % (SECTOR // 4):
        minifat.append(FREESECT)

    # ---- lay out regular sectors ----
    # order: [regular streams][mini-stream container][mini-FAT][directory] then FAT/DIFAT
    sectors = []                       # list of 512-byte blobs

    def add_sectors(payload):
        first = len(sectors)
        pad = payload + b"\x00" * (-len(payload) % SECTOR)
        for i in range(0, len(pad), SECTOR):
            sectors.append(pad[i:i + SECTOR])
        return first, (len(pad) // SECTOR)

    def chain(first, count):
        for k in range(count):
            fat.append(first + k + 1 if k < count - 1 else ENDOFCHAIN)

    # regular streams (size >= cutoff)
    for e in entries:
        if e.etype == 2 and e.size >= MINICUTOFF:
            first, cnt = add_sectors(e.data)
            e.start = first
            chain(first, cnt)
    # mini-stream container (root.start)
    if mini_stream:
        first, cnt = add_sectors(bytes(mini_stream))
        root.start = first
        chain(first, cnt)
    else:
        root.start = ENDOFCHAIN
    # mini-FAT
    minifat_first = ENDOFCHAIN
    minifat_cnt = 0
    if minifat:
        mf = b"".join(struct.pack("<I", v) for v in minifat)
        first, cnt = add_sectors(mf)
        minifat_first, minifat_cnt = first, cnt
        chain(first, cnt)
    # directory
    dir_entries_per_sector = SECTOR // 128
    n_entries = len(entries)
    dir_pad = (-n_entries) % dir_entries_per_sector
    dirbuf = bytearray()
    for e in entries + [None] * dir_pad:
        dirbuf += _dir_entry_bytes(e)
    dir_first, dir_cnt = add_sectors(bytes(dirbuf))
    chain(dir_first, dir_cnt)

    # ---- FAT: needs to cover all data sectors + the FAT sectors themselves ----
    # iterate to a fixed point (FAT sectors count depends on total sector count)
    n_data = len(sectors)
    n_fat = 1
    while True:
        total = n_data + n_fat
        need = (total + (SECTOR // 4) - 1) // (SECTOR // 4)
        if need == n_fat:
            break
        n_fat = need
    # FAT sector indices come right after data sectors
    fat_start = n_data
    while len(fat) < n_data:
        fat.append(FREESECT)
    for i in range(n_fat):
        fat.append(FATSECT)               # mark FAT sectors
    # pad fat to full sectors
    while len(fat) % (SECTOR // 4):
        fat.append(FREESECT)
    fat_bytes = b"".join(struct.pack("<I", v) for v in fat)

    # ---- header ----
    header = bytearray(b"\x00" * SECTOR)
    header[0:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    header[24:26] = struct.pack("<H", 0x003E)     # minor version
    header[26:28] = struct.pack("<H", 0x0003)     # major version 3
    header[28:30] = struct.pack("<H", 0xFFFE)     # byte order
    header[30:32] = struct.pack("<H", 9)          # sector shift 2^9=512
    header[32:34] = struct.pack("<H", 6)          # mini sector shift 2^6=64
    struct.pack_into("<I", header, 44, n_fat)     # # FAT sectors
    struct.pack_into("<I", header, 48, dir_first)  # first dir sector
    struct.pack_into("<I", header, 56, MINICUTOFF)
    struct.pack_into("<I", header, 60, minifat_first)
    struct.pack_into("<I", header, 64, minifat_cnt)
    struct.pack_into("<I", header, 68, ENDOFCHAIN)  # first DIFAT
    struct.pack_into("<I", header, 72, 0)           # # DIFAT sectors
    # DIFAT array (109 entries) at offset 76: list the FAT sector numbers
    for i in range(109):
        v = (fat_start + i) if i < n_fat else FREESECT
        struct.pack_into("<I", header, 76 + 4 * i, v)

    out = bytearray(header)
    for s in sectors:
        out += s
    out += fat_bytes
    return bytes(out)


def _dir_entry_bytes(e) -> bytes:
    b = bytearray(b"\x00" * 128)
    if e is None:
        struct.pack_into("<I", b, 0x44, NOSTREAM)
        struct.pack_into("<I", b, 0x48, NOSTREAM)
        struct.pack_into("<I", b, 0x4C, NOSTREAM)
        return bytes(b)
    nm = e.name.encode("utf-16-le") + b"\x00\x00"
    b[0:len(nm)] = nm
    struct.pack_into("<H", b, 0x40, len(nm))
    b[0x42] = e.etype
    b[0x43] = e.color
    struct.pack_into("<I", b, 0x44, e.left)
    struct.pack_into("<I", b, 0x48, e.right)
    struct.pack_into("<I", b, 0x4C, e.child)
    b[0x50:0x60] = e.clsid
    struct.pack_into("<I", b, 0x74, e.start)
    struct.pack_into("<Q", b, 0x78, e.size)
    return bytes(b)
