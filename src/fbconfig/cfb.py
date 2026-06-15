"""Minimal OLE2/CFB (MS-CFB) helper: resize a small (mini-FAT) stream IN PLACE,
within its already-allocated mini-sectors (no new allocation, so the file's sector
layout/total size is unchanged). Used to set the PROFINET device name in the SyCon
project blob's `PNIODeviceDataModelBasic` stream, where SyCon stores the name as a
length-prefixed field at the stream start and keeps the name tight (a non-tight
length makes SyCon error). olefile parses the structure (read-only); we compute the
mini-sector -> byte mapping and patch the bytes + the directory size field ourselves.

Only the in-slack case is handled (new size <= allocated mini-sectors); growing the
allocation (very long names) returns None so the caller can fall back safely.
"""
from __future__ import annotations
import io
import struct
import olefile

_FREESECT = {0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC}


def _fat_chain(fat, start):
    chain, s = [], start
    while s not in _FREESECT and 0 <= s < len(fat) and len(chain) < 100000:
        chain.append(s)
        s = fat[s]
    return chain


_ENDOFCHAIN = 0xFFFFFFFE


def _find_de(ole, name):
    """Resolve a stream by leaf name OR full path ('A/B/C') to its directory entry.
    Path-aware so nested streams that share a leaf name (e.g. several per-signal
    `PNIODataBasic`) can be addressed unambiguously."""
    try:
        return ole.direntries[ole._find(name)]
    except Exception:
        return None


def resize_stream(cfb: bytes, stream_name: str, new_content: bytes):
    """Resize ANY stream to `new_content`, picking the right path:
      • mini stream (size < minisectorcutoff, stays < cutoff) -> resize_ministream
      • regular stream (>= cutoff) -> grow/shrink within its regular-FAT chain, taking
        free sectors from the FAT pool when growing (the file's total size is unchanged
        because free sectors already exist physically), updating the directory size.
    Returns None (caller falls back safely) for the unhandled mini<->regular crossover
    or if there aren't enough free sectors to grow."""
    ole = olefile.OleFileIO(io.BytesIO(cfb))
    try:
        cutoff = ole.minisectorcutoff
        de = _find_de(ole, stream_name)
        if de is None:
            return None
        if de.size < cutoff:
            if len(new_content) < cutoff:
                ole.close()
                return resize_ministream(cfb, stream_name, new_content)
            return None                                   # mini -> regular crossover
        if len(new_content) < cutoff:
            return None                                   # regular -> mini crossover
        ss = ole.sectorsize
        fat = list(ole.fat)
        chain = _fat_chain(fat, de.isectStart)
        need = max(1, (len(new_content) + ss - 1) // ss)
        csect_fat = struct.unpack_from("<I", cfb, 44)[0]
        difat = [struct.unpack_from("<I", cfb, 76 + 4 * i)[0]
                 for i in range(min(109, csect_fat))]      # FAT sector locations
        dir_start = struct.unpack_from("<I", cfb, 48)[0]
        dirchain = _fat_chain(fat, dir_start)
        dir_byte = 512 + dirchain[(de.sid * 128) // ss] * ss + ((de.sid * 128) % ss)
        changed_fat = {}
        if need > len(chain):
            freelist = [i for i, v in enumerate(fat) if v == 0xFFFFFFFF]
            if len(freelist) < need - len(chain):
                return None                               # no room — fall back
            add = freelist[:need - len(chain)]
            prev = chain[-1]
            for n in add:
                fat[prev] = n; changed_fat[prev] = n; prev = n
            fat[prev] = _ENDOFCHAIN; changed_fat[prev] = _ENDOFCHAIN
            chain += add
        elif need < len(chain):
            for n in chain[need:]:                        # free the tail sectors
                changed_fat[n] = 0xFFFFFFFF
            changed_fat[chain[need - 1]] = _ENDOFCHAIN
            chain = chain[:need]
    finally:
        ole.close()

    def fat_byte(i):
        per = ss // 4
        return 512 + difat[i // per] * ss + (i % per) * 4

    out = bytearray(cfb)
    capacity = len(chain) * ss
    padded = new_content + b"\x00" * (capacity - len(new_content))
    for k, n in enumerate(chain):
        out[512 + n * ss:512 + n * ss + ss] = padded[k * ss:(k + 1) * ss]
    for i, v in changed_fat.items():
        struct.pack_into("<I", out, fat_byte(i), v)
    struct.pack_into("<I", out, dir_byte + 120, len(new_content))
    try:
        chk = olefile.OleFileIO(io.BytesIO(bytes(out)))
        ok = chk.openstream(stream_name).read() == new_content
        chk.close()
    except Exception:
        return None
    return bytes(out) if ok else None


def resize_ministream(cfb: bytes, stream_name: str, new_content: bytes):
    """Return a new CFB with `stream_name` (a mini-FAT stream) set to `new_content`,
    growing/shrinking the stream within its mini-sectors and updating the directory
    size. If the content needs more mini-sectors than allocated, free mini-sectors
    from the container pool are appended to the chain (the file's regular sectors /
    total size stay the same). Returns None if the stream isn't a mini stream, isn't
    found, or there aren't enough free mini-sectors (caller falls back safely)."""
    ole = olefile.OleFileIO(io.BytesIO(cfb))
    try:
        ss, mss = ole.sectorsize, ole.minisectorsize
        ole.loadminifat()
        minifat = list(ole.minifat)
        de = _find_de(ole, stream_name)
        if de is None or de.size >= ole.minisectorcutoff:
            return None                                   # not a mini stream
        cont = _fat_chain(ole.fat, ole.root.isectStart)   # mini-stream container sectors
        cont_minicount = len(cont) * ss // mss            # mini-sectors the container holds
        mini = []                                         # the stream's mini-sectors
        s = de.isectStart
        while s not in _FREESECT and len(mini) < 100000:
            mini.append(s)
            s = minifat[s]
        # mini-FAT byte locations (mini-FAT is a normal-FAT stream from header@60).
        mfat_start = struct.unpack_from("<I", cfb, 60)[0]
        mfat_chain = _fat_chain(ole.fat, mfat_start)
        mfat_capacity = len(mfat_chain) * (ss // 4)        # entries the mini-FAT can hold
        root_size = ole.root.size
        need = max(1, (len(new_content) + mss - 1) // mss)   # mini-sectors needed
        grow_root = 0
        if need > len(mini):
            # free mini-sectors: unused entries in the mini-FAT, OR slots that exist in
            # the container but past the current mini-stream end (container slack).
            free = [i for i in range(min(len(minifat), cont_minicount))
                    if minifat[i] == 0xFFFFFFFF]
            slack = [i for i in range(len(minifat), cont_minicount)
                     if i < mfat_capacity]                # extendable mini-FAT entries
            pool = free + slack
            if len(pool) < need - len(mini):
                return None                               # not enough room — fall back
            add = pool[:need - len(mini)]
            while len(minifat) <= max(add):               # extend the in-memory mini-FAT
                minifat.append(0xFFFFFFFF)
            prev = mini[-1]
            for n in add:                                 # link new sectors onto the chain
                minifat[prev] = n
                prev = n
            minifat[prev] = _ENDOFCHAIN
            mini += add
            # the mini-stream (root) must cover the highest mini-sector used
            need_root = (max(mini) + 1) * mss
            if need_root > root_size:
                grow_root = need_root
        # directory entry byte offset (directory is a normal-FAT stream from header@48)
        dir_start = struct.unpack_from("<I", cfb, 48)[0]
        dirchain = _fat_chain(ole.fat, dir_start)
        dir_byte = 512 + dirchain[(de.sid * 128) // ss] * ss + ((de.sid * 128) % ss)
        root_byte = 512 + dirchain[(ole.root.sid * 128) // ss] * ss \
            + ((ole.root.sid * 128) % ss)
        orig_minifat = list(ole.minifat)
    finally:
        ole.close()

    def mini_byte(n):                                     # blob offset of mini-sector n
        cb = n * mss
        return 512 + cont[cb // ss] * ss + (cb % ss)

    def mfat_byte(i):                                     # blob offset of mini-FAT entry i
        b = i * 4
        return 512 + mfat_chain[b // ss] * ss + (b % ss)

    out = bytearray(cfb)
    capacity = len(mini) * mss
    padded = new_content + b"\x00" * (capacity - len(new_content))   # zero the slack
    for k, n in enumerate(mini):
        out[mini_byte(n):mini_byte(n) + mss] = padded[k * mss:(k + 1) * mss]
    for i, v in enumerate(minifat):                       # write changed + new entries
        if i >= len(orig_minifat) or v != orig_minifat[i]:
            struct.pack_into("<I", out, mfat_byte(i), v)
    struct.pack_into("<I", out, dir_byte + 120, len(new_content))    # directory size (u32)
    if grow_root:                                         # mini-stream container grew
        struct.pack_into("<I", out, root_byte + 120, grow_root)
    # sanity: re-open + read the stream back, verify content + structure intact
    try:
        chk = olefile.OleFileIO(io.BytesIO(bytes(out)))
        ok = chk.openstream(stream_name).read() == new_content
        chk.close()
    except Exception:
        return None
    return bytes(out) if ok else None
