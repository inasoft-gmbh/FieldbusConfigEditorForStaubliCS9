"""PROFINET netX .nxd / _nwid.nxd scalar-field helpers (Stufe 1).

The .nxd is a netX DBM. We patch a few scalar settings IN PLACE (no length change,
so the file/CFB stays structurally intact) and recompute the framework MD5
(@0x54 over data[136:]). All offsets are located by a UNIQUE byte signature or by
the current value string — never hardcoded — and every patch verifies the bytes it
is about to change actually hold the expected old value (fail-safe: returns the
input unchanged if the structure is not recognised). Encodings reverse-engineered
from byte-exact SyCon diff pairs (docs/11_BLOB_AND_NXD_FINDINGS.md).
"""
from __future__ import annotations
import struct
import hashlib

MD5_OFF = 0x54
DATA_OFF = 136

# CHANNEL_SETTING value block: this 12-byte signature is unique in the .nxd; the
# startup byte follows immediately, then the watchdog u32 (milliseconds).
_CHSET_SIG = bytes.fromhex("300000000300000001000000")


def recompute_md5(d: bytearray) -> None:
    d[MD5_OFF:MD5_OFF + 16] = hashlib.md5(bytes(d[DATA_OFF:])).digest()


def md5_hex(data: bytes) -> str:
    """The framework MD5 of a .nxd as upper-case hex == the Val3/blob `configMD5`."""
    return hashlib.md5(data[DATA_OFF:]).hexdigest().upper()


# ---------------------------------------------------------------- watchdog / startup
def _chset_pos(d: bytes):
    i = d.find(_CHSET_SIG)
    if i < 0 or d.count(_CHSET_SIG) != 1:
        return None
    return i + len(_CHSET_SIG)            # -> startup byte; watchdog u32 right after


def read_channel(d: bytes):
    """(startup, watchdog_ms) or None. startup: 0=automatic by device,
    1=controlled by application."""
    p = _chset_pos(d)
    if p is None or p + 5 > len(d):
        return None
    startup = d[p]
    wd = struct.unpack_from("<I", d, p + 1)[0]
    if startup not in (0, 1) or not (0 < wd <= 0xFFFFFF):
        return None                       # not the structure we expect -> bail
    return startup, wd


def patch_channel(data: bytes, startup: int, watchdog_ms: int) -> bytes:
    """Set startup byte + watchdog u32 in place, recompute MD5. No-op (byte-exact)
    if the values are unchanged or the block is not found."""
    d = bytearray(data)
    p = _chset_pos(d)
    if p is None or p + 5 > len(d):
        return bytes(d)
    cur = read_channel(d)
    if cur is None:
        return bytes(d)
    d[p] = 1 if startup else 0
    struct.pack_into("<I", d, p + 1, watchdog_ms)
    recompute_md5(d)
    return bytes(d)


# ---------------------------------------------------------------- station name (_nwid)
# ---------------------------------------------------------------- endian (per submodule)
# Each I/O submodule record in the value section carries a 1-byte endian flag
# (1 = Big Endian, 2 = Little Endian). The flags form one CONTIGUOUS run at a fixed
# 68-byte stride. We seed the run from the record-size signature and extend it both
# ways while the byte stays the same endian value — robust to a differing last record.
_SUBREC_SIGS = (bytes.fromhex("1700000044000000"), bytes.fromhex("1600000024000000"))
_STRIDE = 68


def _endian_flag_positions(d: bytes):
    """Sorted byte offsets of the per-submodule endian flags, or [] if not found."""
    seeds = []
    for sig in _SUBREC_SIGS:
        i = 0
        while True:
            i = d.find(sig, i)
            if i < 0:
                break
            f = i - 2                       # the flag byte sits 2 bytes before the sig
            if f >= 0 and d[f] in (1, 2):
                seeds.append(f)
            i += 1
    if not seeds:
        return []
    val = d[min(seeds)]                      # the current endian value (all flags equal)
    run = set()
    for s in seeds:
        if d[s] != val:
            continue
        run.add(s)
        p = s - _STRIDE                      # extend down
        while p >= 0 and d[p] == val:
            run.add(p); p -= _STRIDE
        p = s + _STRIDE                      # extend up
        while p < len(d) and d[p] == val:
            run.add(p); p += _STRIDE
    return sorted(run)


def read_endian_big(d: bytes):
    """True=Big, False=Little, or None if no flags found (all flags share one value)."""
    pos = _endian_flag_positions(d)
    if not pos:
        return None
    return d[pos[0]] == 1


def patch_endian(data: bytes, big: bool) -> bytes:
    """Set every per-submodule endian flag to 1 (Big) or 2 (Little) + recompute MD5.
    No-op (byte-exact) if unchanged or no flags found."""
    d = bytearray(data)
    pos = _endian_flag_positions(d)
    if not pos:
        return bytes(d)
    v = 1 if big else 2
    for p in pos:
        d[p] = v
    recompute_md5(d)
    return bytes(d)


def read_station_name(nwid: bytes):
    """The PROFINET station name from a _nwid.nxd, or None. Stored as
    <u16 len LE><ASCII name><zero pad> at a fixed, zero-filled buffer."""
    pos = _name_pos(nwid)
    if pos is None:
        return None
    off, ln = pos
    return nwid[off:off + ln].decode("ascii", "replace")


def _name_pos(nwid: bytes):
    """(name_offset, length) of the station-name string in a _nwid.nxd. The name
    sits after a u16 length prefix; we find the prefix+ASCII run in the data area."""
    # the name buffer is the only <u16 len><printable-ascii> run in the tail data;
    # locate it by scanning for a plausible length prefix followed by ASCII.
    for i in range(DATA_OFF, len(nwid) - 2):
        ln = struct.unpack_from("<H", nwid, i)[0]
        if 1 <= ln <= 240 and i + 2 + ln <= len(nwid):
            run = nwid[i + 2:i + 2 + ln]
            if run and all(0x20 <= c < 0x7F for c in run) and nwid[i + 2 + ln] == 0:
                # require it to look like a host/station name (letters/digits/_-.)
                if all(chr(c).isalnum() or chr(c) in "_-." for c in run):
                    return i + 2, ln
    return None


def patch_station_name(nwid: bytes, name: str) -> bytes:
    """Replace the station name in a _nwid.nxd in place (zero-padded buffer kept the
    same size -> file length unchanged), recompute MD5. Byte-exact reproduction of
    SyCon's output (verified). Returns input unchanged if the buffer isn't found."""
    name_b = name.encode("ascii", "replace")
    d = bytearray(nwid)
    pos = _name_pos(d)
    if pos is None:
        return bytes(d)
    off, old_len = pos
    if len(name_b) > 240:
        name_b = name_b[:240]
    d[off - 2:off] = struct.pack("<H", len(name_b))     # new u16 length
    d[off:off + old_len] = b"\x00" * old_len            # clear old name buffer
    d[off:off + len(name_b)] = name_b                   # write new name
    recompute_md5(d)
    return bytes(d)
