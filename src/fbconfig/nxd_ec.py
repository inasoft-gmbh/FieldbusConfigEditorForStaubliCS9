"""EtherCAT netX .nxd "General Settings" scalar helpers (Stufe 1).

Mirrors nxd_pn: patch a few scalar settings IN PLACE (no length change -> file/CFB
stays intact) and recompute the framework MD5 (@0x54 over data[136:]). Offsets are
located by the settings-record signature + a plausibility check, NEVER hardcoded;
patches are fail-safe (return the input unchanged if the block isn't recognised).
Encodings reverse-engineered from byte-exact SyCon diff pairs (one field at a time;
see the RE log). Fields = SyCon's EtherCAT "General Settings" dialog:
  Interface : Bus Startup (0=Automatic, 1=Controlled), Watchdog Time (ms)
  Ident     : Vendor ID, Product Code, Revision Number, Serial Number
  Data      : SyncImpulseLength (x10 ns), Station Alias
(I/O Data Status is NOT in the export; Output/Input Data Bytes are structural.)
"""
from __future__ import annotations
import struct
import hashlib

MD5_OFF = 0x54
DATA_OFF = 136

# Settings record header: type 0x17, size 0x74 (=116 bytes). Several 0x17/0x74
# records exist in the .nxd; the General-Settings one is the (unique) occurrence
# whose scalar block right after it holds plausible values.
_SIG = bytes.fromhex("1700000074000000")

# field offsets relative to the block base (= sig_pos + 16):
_O = {
    "bus_startup":  (0,  "<I"),   # 0=Automatic, 1=Controlled
    "watchdog_ms":  (4,  "<I"),   # milliseconds
    "vendor_id":    (8,  "<I"),
    "product_code": (12, "<I"),
    "revision":     (16, "<I"),
    "serial":       (20, "<I"),
    "sync_x10ns":   (25, "<H"),   # SyncImpulseLength, units of 10 ns
    "station_alias":(43, "<H"),
}
_BLOCK_END = 45   # bytes needed after base


def recompute_md5(d: bytearray) -> None:
    d[MD5_OFF:MD5_OFF + 16] = hashlib.md5(bytes(d[DATA_OFF:])).digest()


def md5_hex(data: bytes) -> str:
    """Framework MD5 of a .nxd as UPPER-case hex == the export .xml `configMD5`."""
    return hashlib.md5(data[DATA_OFF:]).hexdigest().upper()


def _block_base(d: bytes):
    """Byte offset of the General-Settings scalar block, or None. Among all settings-
    record signatures, the real one is the single occurrence whose startup/watchdog/
    vendor/product look like plausible settings (the others carry GUID-like bytes)."""
    cands = []
    i = 0
    while True:
        i = d.find(_SIG, i)
        if i < 0:
            break
        b = i + 16
        if b + _BLOCK_END <= len(d):
            su = struct.unpack_from("<I", d, b)[0]
            wd = struct.unpack_from("<I", d, b + 4)[0]
            ven = struct.unpack_from("<I", d, b + 8)[0]
            prod = struct.unpack_from("<I", d, b + 12)[0]
            if su in (0, 1) and 0 < wd <= 0xFFFFFF and 0 < ven <= 0xFFFFFF and 0 < prod <= 0xFFFFFF:
                cands.append(b)
        i += 1
    return cands[0] if len(cands) == 1 else None


def read_general(d: bytes):
    """Dict of the EtherCAT general settings, or None if the block isn't found."""
    b = _block_base(d)
    if b is None:
        return None
    return {k: struct.unpack_from(fmt, d, b + off)[0] for k, (off, fmt) in _O.items()}


def patch_general(data: bytes, fields: dict) -> bytes:
    """Patch the given subset of general-settings fields in place + recompute MD5.
    Byte-exact no-op if the block isn't found. `bus_startup` is coerced to 0/1.
    Unknown keys are ignored; values are masked to their field width."""
    d = bytearray(data)
    b = _block_base(d)
    if b is None:
        return bytes(d)
    for k, v in fields.items():
        if k not in _O or v is None:
            continue
        off, fmt = _O[k]
        if k == "bus_startup":
            v = 1 if v else 0
        mask = 0xFFFFFFFF if fmt == "<I" else 0xFFFF
        struct.pack_into(fmt, d, b + off, int(v) & mask)
    recompute_md5(d)
    return bytes(d)
