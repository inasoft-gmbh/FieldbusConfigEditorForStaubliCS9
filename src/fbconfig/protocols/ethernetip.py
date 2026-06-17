"""EtherNet/IP adapter plugin (Hilscher NETX 51 RE/EIS).

Differences from POWERLINK (same outer framework: hex-blob + u32 detail-length):
  * ONE detail module ("Connect1", moduleAddress "Slot 1"); direction is the
    per-signal signalType ("input" first, then "output"), each 0-based.
  * Bit-granular: one <Signal> per bit (arrayElements=1, signalAccessPath
    "byte.bit"); Property 6103 = bit offset (LE u32). Other types are whole bytes.
  * Data types: bit, signed8/16/32, real32.
  * .nxd field offsets differ from POWERLINK (lengths are NOT @324/@326); the
    framework MD5 @0x54 still holds. Node/IP come from the Val3 adapter export.

Read is byte-validated against the real project. Write regenerates only the
detail block (leading OLE2 + trailing CIP assembly kept verbatim), so identity
and same-size edits round-trip byte-exact; size changes need a SyCon skeleton.
"""
from __future__ import annotations
import re
import struct
import base64
import hashlib

import struct as _struct

from .. import sycon
from ..model import ConfigModel, Interface, Signal, DeviceInfo
from ..datatypes import bit_width, by_sycon
from ..writers import _guid_to_6100, _off_b64, _sub

CONNECT_ANCHOR = sycon.ANCHOR   # '<Module  systemTag="' (two spaces)


def _bit_offset_from_6103(sig_xml: str) -> int | None:
    m = re.search(r'id="6103"[^>]*value="([^"]*)"', sig_xml)
    if not m:
        return None
    return struct.unpack("<I", base64.b64decode(m.group(1)))[0]


def _bitoff_from_accesspath(ap: str) -> int:
    b, _, bit = ap.partition(".")
    return int(b) * 8 + (int(bit) if bit else 0)


def parse_signals(detail_text: str, profinet: bool = False):
    """All <Signal> in document order, as (sig, type). bit_offset is the GLOBAL
    bit offset. For PROFINET that comes from signalAccessPath (the 6103 property is
    MODULE-LOCAL there — see docs/08 §5b); for the others from 6103."""
    out = []
    for sm in re.finditer(r"<Signal\b.*?</Signal>", detail_text, re.S):
        s = sm.group(0)
        g = lambda k: (re.search(rf'{k}="([^"]*)"', s) or [None, ""])[1]
        if profinet:
            bitoff = _bitoff_from_accesspath(g("signalAccessPath"))
        else:
            bitoff = _bit_offset_from_6103(s)
            if bitoff is None:                   # fall back to accessPath
                bitoff = _bitoff_from_accesspath(g("signalAccessPath"))
        out.append((Signal(
            name=g("displayName"),
            sycon_dtype=g("dataType"),
            array_elements=int(g("arrayElements") or "1"),
            systemtag=g("systemTag"),
            signal_type=g("signalType"),
            bit_offset=bitoff,
        ), g("signalType")))
    return out


def _direction_bytes(sigs) -> int:
    """Total bytes a direction spans = highest bit end rounded up to a byte."""
    end = 0
    for s in sigs:
        end = max(end, s.bit_offset + s.bits)
    return (end + 7) // 8


def _val3_device(paths) -> dict:
    """Pull the network identity from the Val3 adapter export. `stationAddress`
    carries it per protocol: 'IP a.b.c.d' (EtherNet/IP), a number (EtherCAT
    station), 'Addr <name>' (PROFINET device name)."""
    info = {}
    if paths.val3_xml and paths.val3_xml.is_file():
        t = paths.val3_xml.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'stationAddress="([^"]*)"', t)
        if m:
            info["station"] = m.group(1)
        m = re.search(r'<Adapter\b[^>]*displayName="([^"]*)"', t)
        if m:
            info["adapter"] = m.group(1)
        m = re.search(r'configMD5="([0-9A-Fa-f]+)"', t)
        if m:
            info["config_md5"] = m.group(1)
        m = re.search(r'byteOrder="([^"]*)"', t)
        if m:
            info["byte_order"] = m.group(1)        # 'big' / 'little' (PROFINET endian)
    return info


def parse_station(raw: str, protocol: str) -> dict:
    """Split a Val3 stationAddress into editable fields by protocol."""
    p = (protocol or "").lower()
    raw = raw or ""
    if "ethernet" in p or "/ip" in p or "eip" in p:
        ip = raw[3:].strip() if raw.startswith("IP ") else ""
        return {"kind": "ip", "ip": ip}
    if "profinet" in p or "pns" in p:
        # SyCon writes the PROFINET device name as `Addr <name>` (two d). Accept the
        # one-d `Adr ` spelling too — older builds of THIS tool wrote it (a bug).
        low = raw.lower()
        if low.startswith("adr "):
            name = raw[4:].strip()
        elif low.startswith("addr "):
            name = raw[5:].strip()
        else:
            name = raw
        return {"kind": "name", "name": name}
    if "ethercat" in p or "ecs" in p:
        return {"kind": "station", "station": raw}
    return {"kind": "raw", "raw": raw}


def build_station(fields: dict) -> str:
    """Inverse of parse_station: build the stationAddress string."""
    k = fields.get("kind")
    if k == "ip":
        return f"IP {fields['ip']}"
    if k == "name":
        return f"Addr {fields['name']}"         # SyCon uses "Addr " (two d)
    if k == "station":
        return fields["station"]
    return fields.get("raw", "")


def _station_info(paths, protocol, dev) -> dict:
    """Read the network identity from Val3, apply it to `dev` for display, and
    return the raw bits stored in model.raw (for the General editor + writer)."""
    v = _val3_device(paths)
    fields = parse_station(v.get("station", ""), protocol)
    if fields["kind"] == "ip":
        dev.ip = fields["ip"]
    elif fields["kind"] == "name":
        dev.node_name = fields["name"]
    elif fields["kind"] == "station":
        try:
            dev.node_id = int(fields["station"])
        except ValueError:
            dev.node_name = fields["station"]
    return {"station_raw": v.get("station", ""), "station_fields": fields,
            "adapter": v.get("adapter", ""), "config_md5": v.get("config_md5", ""),
            "byte_order": v.get("byte_order", "big")}


# EtherNet/IP .nxd: the configured assembly size lives here as a BIT count
# (validated stable across per-bit, byte-packed and 64B/104B variants).
OFF_IN_BITS, OFF_OUT_BITS = 1141, 1181

# EtherCAT .nxd: the configured process-image size lives here as a BYTE count
# (validated across 11 projects, sizes 1..208 B incl. asymmetric In/Out). This is
# the FIXED image size — like EtherNet/IP, add/delete operate WITHIN it and the
# .nxd is left untouched; only a size change (resize) would need a new .nxd.
OFF_EC_IN_BYTES, OFF_EC_OUT_BYTES = 380, 408


def _ethercat_nxd_sizes(paths):
    """(in_bytes, out_bytes) configured process-image size from an EtherCAT .nxd,
    or (None, None) if unavailable."""
    if not (paths.nxd and paths.nxd.is_file()):
        return None, None
    d = paths.nxd.read_bytes()
    if len(d) < OFF_EC_OUT_BYTES + 2:
        return None, None
    return (struct.unpack_from("<H", d, OFF_EC_IN_BYTES)[0],
            struct.unpack_from("<H", d, OFF_EC_OUT_BYTES)[0])


def _ethercat_nxd_resized(nxd_path, in_bytes, out_bytes) -> bytes:
    """The .nxd with its process-image size set to (in_bytes, out_bytes) @380/@408
    and the MD5 (@0x54 over data[136:]) recomputed. Only those 20 bytes change, so
    a no-op resize reproduces the file byte-exact (like POWERLINK @324/@326)."""
    d = bytearray(open(nxd_path, "rb").read())
    if len(d) < OFF_EC_OUT_BYTES + 2:
        return bytes(d)
    struct.pack_into("<H", d, OFF_EC_IN_BYTES, in_bytes)
    struct.pack_into("<H", d, OFF_EC_OUT_BYTES, out_bytes)
    d[0x54:0x54 + 16] = hashlib.md5(bytes(d[136:])).digest()
    return bytes(d)


def _nxd_info(paths) -> dict:
    """Framework-level .nxd check (MD5 @0x54 over data[136:]) plus the authoritative
    assembly size in bits (@1141 input, @1181 output)."""
    if not (paths.nxd and paths.nxd.is_file()):
        return {}
    d = paths.nxd.read_bytes()
    md5_ok = hashlib.md5(d[136:]).digest() == d[0x54:0x54 + 16]
    info = {"size": len(d), "md5_ok": md5_ok}
    if len(d) > OFF_OUT_BITS + 2:
        info["input_bytes"] = struct.unpack_from("<H", d, OFF_IN_BITS)[0] // 8
        info["output_bytes"] = struct.unpack_from("<H", d, OFF_OUT_BITS)[0] // 8
    return info


def _load_eip_eis(paths, blob) -> ConfigModel:
    """EtherNet/IP NETX 51 RE/EIS variant: signals are UTF-16 XML in the CFB stream
    EISAdapterBasic (no appended detail block), the assembly SIZE is in CIPConnectBasic.
    Full structural editing is supported via blob_eip (signal edits touch only that stream)."""
    from .. import blob_eip
    sigs = blob_eip.read_signals(blob)
    in_max, out_max = blob_eip.assembly_sizes(blob)
    in_sigs, out_sigs = [], []
    for st, name, dtype, ae, ap, tag in sigs:
        s = Signal(name=name, sycon_dtype=dtype, array_elements=int(ae or 1),
                   systemtag=tag, signal_type=st, bit_offset=int(ap or 0) * 8)
        (in_sigs if st == "input" else out_sigs).append(s)
    dev = DeviceInfo(protocol="EtherNet/IP", base_name=paths.base_name)
    sinfo = _station_info(paths, "EtherNet/IP", dev)
    inp = Interface("In", max(in_max or 0, _direction_bytes(in_sigs)), signals=in_sigs)
    out = Interface("Out", max(out_max or 0, _direction_bytes(out_sigs)), signals=out_sigs)
    model = ConfigModel(dev, inp, out, raw={
        "protocol_kind": "ethernetip",
        "bit_addressed": True,
        "eip_eis": True,                          # marker for the structural writer
        # add/delete/edit/reorder are size-invariant (touch only EISAdapterBasic). Resize
        # writes the size to the .nxd (@1141/@1181 + integrity MD5 = what the robot reads)
        # and the blob CIPConnectBasic; the SyCon-internal configMD5 (= f(sizes)) is not
        # reproduced -> re-validate a resize in SyCon.net (B1).
        "structural": True, "reorderable": True, "resizable": True,
        "paths": paths,
        "orig_names": {s.systemtag: s.name for s in in_sigs + out_sigs},
        **sinfo,
    })
    nx = _nxd_info(paths)
    if nx:
        model.raw["nxd"] = nx
    if sinfo.get("config_md5"):
        model.raw["config_md5"] = sinfo["config_md5"]
    return model


def load(paths) -> ConfigModel:
    xml = sycon.read_xml(paths.sycon_xml)
    blob = sycon.blob_from_xml(xml)
    # NETX 51 RE/EIS variant: signals are an XML in the CFB stream EISAdapterBasic (detection
    # by the stream, NOT detail_block — for the clean format detail_block coincidentally
    # succeeds, but its in-place byte-replace would corrupt the CFB; blob_eip rebuilds it).
    if b"E\x00I\x00S\x00A\x00d\x00a\x00p\x00t\x00e\x00r" in blob:
        return _load_eip_eis(paths, blob)

    anchor, declen, detail = sycon.detail_block(blob)
    pairs = parse_signals(detail)
    in_sigs = [s for s, t in pairs if t == "input"]
    out_sigs = [s for s, t in pairs if t == "output"]

    dev = DeviceInfo(protocol="EtherNet/IP", base_name=paths.base_name)
    sinfo = _station_info(paths, "EtherNet/IP", dev)

    # authoritative assembly size from the .nxd; fall back to the signal span
    nx = _nxd_info(paths)
    in_max = nx.get("input_bytes") or _direction_bytes(in_sigs)
    out_max = nx.get("output_bytes") or _direction_bytes(out_sigs)
    in_max = max(in_max, _direction_bytes(in_sigs))     # never below what's used
    out_max = max(out_max, _direction_bytes(out_sigs))

    inp = Interface("In", in_max, signals=in_sigs)
    out = Interface("Out", out_max, signals=out_sigs)
    model = ConfigModel(dev, inp, out, raw={
        "protocol_kind": "ethernetip",
        "bit_addressed": True,
        # RENAME-ONLY (SyCon-safe) — see generic_load: the assembly/PDO config lives
        # in the binary part of the blob, not rebuilt yet, so a structural edit would
        # break SyCon loading. Add/Delete/Reorder/Resize stay disabled until task B.
        "structural": False,
        "reorderable": False,
        "resizable": False,
        "sycon_path": str(paths.sycon_xml),
        "detail_anchor": anchor,
        "paths": paths,
        "orig_names": {s.systemtag: s.name for s in in_sigs + out_sigs},
        **sinfo,
    })
    if nx:
        model.raw["nxd"] = nx
    if sinfo.get("config_md5"):
        model.raw["config_md5"] = sinfo["config_md5"]
    return model


# ============================================================ writers
# Strategy (same low-risk method as POWERLINK): use the existing files as a
# skeleton, clone per-dataType signal templates verbatim and substitute only
# name / UID / signalType / accessPath / dataType / arrayElements / 6100 / 6103.
# Leading OLE2 header and trailing CIP-assembly bytes are kept untouched, so an
# unchanged model round-trips BYTE-EXACT. Editing keeps the 104-byte size, so the
# .nxd and the Val3 adapter header do not change.

def _signal_blocks(detail_text: str):
    """(prefix, [(leading_ws, body, uid)], suffix) for the Connect module.
    Keeping each signal's EXACT block (and leading whitespace) lets us re-emit by
    matching model signals to their original block by systemTag (UID) — so even
    individually-formatted signals round-trip byte-exact. New signals fall back to
    a per-dataType template."""
    matches = list(re.finditer(r"(\s*)(<Signal\b.*?</Signal>)", detail_text, re.S))
    if not matches:
        raise ValueError("EtherNet/IP: no signals in detail skeleton")
    prefix = detail_text[:matches[0].start()]
    suffix = detail_text[matches[-1].end():]
    blocks = []
    for m in matches:
        uid = re.search(r'systemTag="([^"]+)"', m.group(2)).group(1)
        blocks.append((m.group(1), m.group(2), uid))
    return prefix, blocks, suffix


def _access_path(sig: Signal, profinet: bool = False) -> str:
    """`byte.bit` for sub-byte bit signals; plain `byte` for whole-byte signals.
    EtherNet/IP & EtherCAT: byte-packed bits (arrayElements multiple of 8) use
    plain `byte`. PROFINET: bit signals ALWAYS use `byte.bit` (e.g. "0.0"),
    regardless of arrayElements — its only render difference (see docs/08 §5)."""
    bo = sig.bit_offset or 0
    if by_sycon(sig.sycon_dtype).key == "bit" and (profinet or sig.array_elements % 8):
        return f"{bo // 8}.{bo % 8}"
    return f"{bo // 8}"


def _render_body(body: str, sig: Signal, profinet: bool = False,
                 base_bits: int = 0) -> str:
    """Substitute a signal's fields into a <Signal>...</Signal> block, preserving
    the block's exact formatting (works for both single- and double-quote, any
    whitespace). systemTag may be upper- or lower-case GUID; kept as stored.
    `signalAccessPath` is the GLOBAL offset; the `6103` property is written as
    `bit_offset - base_bits` (PROFINET: module-local; others: base_bits=0 = global)."""
    bo = sig.bit_offset or 0
    ap = _access_path(sig, profinet)
    t = body
    t = _sub("systemTag", sig.systemtag, t)
    t = _sub("displayName", sig.name, t)
    if sig.signal_type:
        t = _sub("signalType", sig.signal_type, t)
    t = _sub("signalAccessPath", ap, t)
    t = _sub("dataType", sig.sycon_dtype, t)
    t = _sub("arrayElements", sig.array_elements, t)
    t = re.sub(r'(id=["\']6100["\'][^>]*value=["\'])[^"\']*(["\'])',
               lambda m: m.group(1) + _guid_to_6100(sig.systemtag) + m.group(2), t, count=1)
    t = re.sub(r'(id=["\']6103["\'][^>]*value=["\'])[^"\']*(["\'])',
               lambda m: m.group(1) + _off_b64(bo - base_bits) + m.group(2), t, count=1)
    return t


def _render_signal(ws: str, body: str, sig: Signal) -> str:
    return ws + _render_body(body, sig)


def _detail_parts(detail: str):
    """(prefix, leads, bodies, suffix). leads[k] is the EXACT content between
    signal k-1 and k (whitespace AND any module boundary `</Module>..<Module>..`),
    so multi-module details (EtherCAT RxPDO/TxPDO, PROFINET slots) reproduce
    byte-exact. leads[0] = '' (the prefix covers up to the first signal)."""
    matches = list(re.finditer(r"<Signal\b.*?</Signal>", detail, re.S))
    if not matches:
        raise ValueError("no signals in detail skeleton")
    prefix = detail[:matches[0].start()]
    suffix = detail[matches[-1].end():]
    leads = [""]
    bodies = [matches[0].group(0)]
    for k in range(1, len(matches)):
        leads.append(detail[matches[k - 1].end():matches[k].start()])
        bodies.append(matches[k].group(0))
    return prefix, leads, bodies, suffix


def _render_name_only(body: str, sig: Signal) -> str:
    """Change ONLY the displayName, leaving systemTag / accessPath / dataType /
    arrayElements / 6100 / 6103 exactly as the skeleton wrote them. Used for
    identity + rename so the result is byte-exact for ANY protocol/variant,
    regardless of how offsets are encoded (e.g. PROFINET per-slot accessPath)."""
    return _sub("displayName", sig.name, body)


def _pn_module_ranges(text):
    """Ordered PROFINET leaf modules as (direction, byte_start, byte_size). The
    byte_start accumulates per direction; size from the "N Byte" displayName,
    direction from the contained signalType (else the inverted module name)."""
    sig_re = re.compile(r"(\s*)(<Signal\b.*?</Signal>)", re.S)
    matches = list(sig_re.finditer(text))
    if not matches:
        return []
    runs, cur = [], [matches[0]]
    for prev, m in zip(matches, matches[1:]):
        gap = text[prev.end():m.start()]
        if "<Module" in gap or "</Module>" in gap:
            runs.append(cur); cur = [m]
        else:
            cur.append(m)
    runs.append(cur)
    ranges, cursor, pos = [], {"input": 0, "output": 0}, 0
    for run in runs:
        sep = text[pos:run[0].start()]
        szs = re.findall(r"""displayName=["']\s*(\d+)\s*Byte""", sep)
        if szs:
            st = re.search(r"""signalType=["'](input|output)["']""", run[0].group(2))
            d = (st.group(1) if st else
                 ("input" if ("Ausgang" in sep or "Output" in sep) else "output"))
            ranges.append((d, cursor[d], int(szs[-1])))
            cursor[d] += int(szs[-1])
        pos = run[-1].end()
    return ranges


def _pn_image_size(text):
    """(in_bytes, out_bytes) PROFINET image = sum of module sizes per direction."""
    r = _pn_module_ranges(text)
    return (sum(sz for d, st, sz in r if d == "input"),
            sum(sz for d, st, sz in r if d == "output"))


def pn_free_runs(model, direction):
    """Sorted (start_byte, length) free runs WITHIN the modules of `direction` —
    i.e. byte ranges not covered by any signal. A new signal must fit inside one
    run (so it never spans a module boundary)."""
    ranges = [(st, sz) for d, st, sz in model.raw.get("pn_modules", []) if d == direction]
    sigs = model.inp.signals if direction == "input" else model.out.signals
    occupied = set()
    for s in sigs:
        b0 = (s.bit_offset or 0) // 8
        for b in range(b0, b0 + max(1, s.bits // 8)):
            occupied.add(b)
    free = []
    for start, size in ranges:
        run = None
        for b in range(start, start + size):
            if b not in occupied:
                run = b if run is None else run
            elif run is not None:
                free.append((run, b - run)); run = None
        if run is not None:
            free.append((run, start + size - run))
    return free


def _pn_find_slot(ranges, occ, nbits, sub_bit, start_bit=0):
    """First free global bit position holding `nbits` free bits WITHIN one module
    (ranges = [(byte_start, byte_size)]), at or after `start_bit`. Byte-aligned
    unless `sub_bit`. Returns None if there is no room."""
    for st, sz in ranges:
        lo, hi = st * 8, (st + sz) * 8
        begin = max(lo, start_bit)
        cands = range(begin, hi) if sub_bit else range(((begin + 7) // 8) * 8, hi, 8)
        for b in cands:
            if b + nbits <= hi and all((b + i) not in occ for i in range(nbits)):
                return b
    return None


def _pn_occupied(sigs, exclude=()):
    occ = set()
    for s in sigs:
        if s in exclude:
            continue
        bo = s.bit_offset or 0
        occ.update(range(bo, bo + s.bits))
    return occ


def pn_add(model, direction, new_sigs):
    """Place new PROFINET signals into free space WITHIN a single module (no
    repack). Sub-byte bit signals take any free bit; all others are byte-aligned.
    Raises ValueError if there is no room."""
    iface = model.inp if direction == "input" else model.out
    ranges = [(st, sz) for d, st, sz in model.raw.get("pn_modules", []) if d == direction]
    for sig in new_sigs:
        sub_bit = by_sycon(sig.sycon_dtype).key == "bit" and sig.array_elements % 8
        pos = _pn_find_slot(ranges, _pn_occupied(iface.signals), sig.bits, sub_bit)
        if pos is None:
            unit = "bit" if sub_bit else f"{max(1, sig.bits // 8)}-byte"
            raise ValueError(
                f"{iface.direction}: no free {unit} slot in any module "
                "(delete signals or resize to a larger module layout first).")
        sig.bit_offset = pos
        iface.signals.append(sig)
    iface.signals.sort(key=lambda s: s.bit_offset or 0)


def pn_relocate(model, direction, moved_sigs, target_byte):
    """Move PROFINET signals to a free slot at/after `target_byte` (else anywhere),
    within a single module. Their old positions become free. Raises if no room."""
    iface = model.inp if direction == "input" else model.out
    ranges = [(st, sz) for d, st, sz in model.raw.get("pn_modules", []) if d == direction]
    occ = _pn_occupied(iface.signals, exclude=moved_sigs)
    cursor = target_byte * 8
    for sig in sorted(moved_sigs, key=lambda s: s.bit_offset or 0):
        sub_bit = by_sycon(sig.sycon_dtype).key == "bit" and sig.array_elements % 8
        pos = _pn_find_slot(ranges, occ, sig.bits, sub_bit, cursor)
        if pos is None:                              # fall back to anywhere free
            pos = _pn_find_slot(ranges, occ, sig.bits, sub_bit, 0)
        if pos is None:
            raise ValueError(f"{iface.direction}: no free slot to move into.")
        sig.bit_offset = pos
        occ.update(range(pos, pos + sig.bits))
        cursor = pos + sig.bits
    iface.signals.sort(key=lambda s: s.bit_offset or 0)


def _render_by_modules(text, model, render_fn):
    """Regenerate the <Signal> blocks of EACH leaf module in place (PROFINET: many
    nested Slot/Subslot modules). Each model signal is placed into the module whose
    direction (signalType) and byte-range (running per-direction cursor; module size
    from the "N Byte …" displayName) contain its global byte offset. Module wrappers
    and any signals outside modules are kept verbatim. See docs/08 §5."""
    sig_re = re.compile(r"(\s*)(<Signal\b.*?</Signal>)", re.S)
    matches = list(sig_re.finditer(text))
    if not matches:
        return text
    # group consecutive signal matches into runs; a gap with <Module/</Module> splits
    runs, cur = [], [matches[0]]
    for prev, m in zip(matches, matches[1:]):
        gap = text[prev.end():m.start()]
        if "<Module" in gap or "</Module>" in gap:
            runs.append(cur); cur = [m]
        else:
            cur.append(m)
    runs.append(cur)

    in_sigs = sorted(model.inp.signals, key=lambda s: s.bit_offset or 0)
    out_sigs = sorted(model.out.signals, key=lambda s: s.bit_offset or 0)
    cursor = {"input": 0, "output": 0}

    out_parts, pos = [], 0
    for run in runs:
        sep = text[pos:run[0].start()]                 # module wrappers before run
        out_parts.append(sep)
        szs = re.findall(r"""displayName=["']\s*(\d+)\s*Byte""", sep)
        size = int(szs[-1]) if szs else None
        if size is None:
            # NOT a sized data module (signals outside modules: diagnostics /
            # F-signals) -> keep the original block verbatim.
            out_parts.append(text[run[0].start():run[-1].end()])
            pos = run[-1].end()
            continue
        st = re.search(r"""signalType=["'](input|output)["']""", run[0].group(2))
        direction = (st.group(1) if st else
                     ("input" if ("Ausgang" in sep or "Output" in sep) else "output"))
        lo, hi = cursor[direction], cursor[direction] + size
        cursor[direction] = hi
        base_bits = lo * 8                             # module start -> local 6103
        pool = in_sigs if direction == "input" else out_sigs
        sel = [s for s in pool if lo <= (s.bit_offset or 0) // 8 < hi]
        by_uid, by_dt = {}, {}
        for mm in run:
            b = mm.group(2)
            by_uid[re.search(r'systemTag=["\']([^"\']+)["\']', b).group(1)] = b
            by_dt.setdefault(re.search(r'dataType=["\'](\w+)["\']', b).group(1), b)
        body_for = lambda s: (by_uid.get(s.systemtag) or by_dt.get(s.sycon_dtype)
                              or run[0].group(2))
        ws0 = run[0].group(1)
        ws1 = run[1].group(1) if len(run) > 1 else ws0
        for j, s in enumerate(sel):
            out_parts.append((ws0 if j == 0 else ws1)
                             + render_fn(body_for(s), s, base_bits))
        pos = run[-1].end()
    out_parts.append(text[pos:])
    return "".join(out_parts)


def _build_detail(model, skeleton_detail: str) -> str:
    prefix, leads, bodies, suffix = _detail_parts(skeleton_detail)
    sig_by_uid = {s.systemtag: s for s in
                  list(model.inp.signals) + list(model.out.signals)}
    body_uids = [re.search(r'systemTag=["\']([^"\']+)["\']', b).group(1)
                 for b in bodies]
    same_set = (len(sig_by_uid) == len(bodies)
                and all(u in sig_by_uid for u in body_uids))

    if same_set and not model.raw.get("layout_dirty"):
        # identity / rename: walk the skeleton in ORIGINAL order; only retouch a
        # block whose name actually CHANGED (vs the name read at load) — so a pure
        # identity save is byte-exact and a rename touches just that signal.
        orig = model.raw.get("orig_names", {})
        parts = [prefix]
        for k, uid in enumerate(body_uids):
            parts.append(leads[k])
            sig = sig_by_uid[uid]
            parts.append(_render_name_only(bodies[k], sig)
                         if sig.name != orig.get(uid, sig.name) else bodies[k])
        parts.append(suffix)
        return "".join(parts)

    # PROFINET: many nested Slot/Subslot modules -> place each signal into the
    # module of its direction whose byte-range holds its offset (docs/08 §5).
    if model.raw.get("protocol_kind") == "profinet":
        return _render_by_modules(
            skeleton_detail, model,
            lambda b, s, base: _render_body(b, s, profinet=True, base_bits=base))

    # layout changed (add/delete/reorder/resize): full render with recomputed
    # offsets. Inter-signal whitespace AND the module boundary are placed by
    # POSITION (input signals | boundary | output signals), NOT by the skeleton's
    # per-signal ws — so EtherCAT's 2 modules (RxPDO input | TxPDO output) stay
    # correct no matter how signals are added/deleted/reordered. A single-module
    # detail (EtherNet/IP) has no boundary lead -> every gap is the normal lead.
    body_by_uid = {body_uids[k]: bodies[k] for k in range(len(bodies))}
    body_by_dt = {}
    for b in bodies:
        body_by_dt.setdefault(re.search(r'dataType=["\'](\w+)["\']', b).group(1), b)

    def body_for(sig):
        return (body_by_uid.get(sig.systemtag) or body_by_dt.get(sig.sycon_dtype)
                or bodies[0])

    boundary = next((l for l in leads[1:] if "</Module>" in l), None)
    normal = next((l for l in leads[1:] if "</Module>" not in l),
                  leads[1] if len(leads) > 1 else "")

    seq = ([("in", s) for s in model.inp.signals]
           + [("out", s) for s in model.out.signals])
    parts, prev = [prefix], None
    for i, (d, sig) in enumerate(seq):
        if i == 0:
            lead = ""                                   # prefix covers the first
        elif prev == "in" and d == "out" and boundary:
            lead = boundary                             # RxPDO -> TxPDO transition
        else:
            lead = normal
        parts.append(lead + _render_body(body_for(sig), sig))
        prev = d
    parts.append(suffix)
    return "".join(parts)


def _identity_change(model):
    """(old_value, new_value) of the in-blob identity string (EtherNet/IP IP,
    PROFINET name), or None. EtherCAT station isn't stored as text in the blob."""
    new_station = model.raw.get("station_new")
    old_station = model.raw.get("station_raw", "")
    if not new_station or new_station == old_station:
        return None
    kind = (model.raw.get("station_fields") or {}).get("kind")
    if kind == "ip":
        return (old_station[3:].strip(), new_station[3:].strip())
    if kind == "name":
        old = old_station[5:].strip() if old_station.lower().startswith("addr ") else old_station
        new = new_station[5:].strip() if new_station.lower().startswith("addr ") else new_station
        return (old, new)
    return None


def _patch_blob_string(blob: bytes, old: str, new: str) -> bytes:
    """Replace a length-prefixed UTF-16 string (`<u32 byteLen><utf16><\\0>`) IN
    PLACE, keeping the field size (zero-fills the slack) so nothing shifts. Only
    if the new value fits the existing field; otherwise returns the blob
    unchanged (the Val3 export still carries the new value)."""
    if not old:
        return blob
    o = blob.find(old.encode("utf-16-le"))
    if o < 4:
        return blob
    region = _struct.unpack_from("<I", blob, o - 4)[0]       # byte length incl term
    if region < len(old) * 2 + 2 or region > 256:           # sanity
        return blob
    nb = new.encode("utf-16-le") + b"\x00\x00"
    if len(nb) > region:                                    # would not fit in place
        return blob
    out = bytearray(blob)
    _struct.pack_into("<I", out, o - 4, len(nb))
    out[o:o + region] = nb + b"\x00" * (region - len(nb))
    return bytes(out)


def write_sycon(model, original_xml_path) -> bytes:
    xml = sycon.read_xml(original_xml_path)
    blob = sycon.blob_from_xml(xml)
    anchor, declen, skel_detail = sycon.detail_block(blob)

    orig_det = blob[anchor:anchor + declen]
    term = orig_det[len(skel_detail.encode("utf-16-le")):]   # exact trailing nulls
    new_det = _build_detail(model, skel_detail).encode("utf-16-le") + term

    out = bytearray(blob[:anchor])
    _struct.pack_into("<I", out, anchor - 4, len(new_det))    # u32 detail length
    out += new_det
    out += blob[anchor + declen:]                            # trailing CIP assembly
    _struct.pack_into("<I", out, 0, len(out) - 4)            # top-level length prefix

    chg = _identity_change(model)                            # IP / name in the blob
    if chg:
        out = bytearray(_patch_blob_string(bytes(out), chg[0], chg[1]))
        _struct.pack_into("<I", out, 0, len(out) - 4)        # size unchanged, but keep

    hexlo = bytes(out).hex()
    return re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                  lambda m: m.group(1) + hexlo + m.group(2), xml, flags=re.S
                  ).encode("utf-8")


# ---- Val3 export (J207J208.xml). Schema differs per protocol, but signals
# always carry a systemTag (UID). For identity/rename we only retouch the
# displayName of the <Signal> blocks whose UID is in the model, leaving every
# other signal (diagnostics: Communication State, Watchdog, ...) untouched. The
# full-render path (layout change) handles offsets and is used only for EtherNet/
# IP, whose Val3 signals carry just a 6103 offset.
def write_val3(model, original_xml_path) -> bytes:
    text = sycon.read_xml(original_xml_path)

    # network identity (General): patch stationAddress (+ EtherNet/IP displayName
    # <ip>) when it changed. Leaves the file byte-exact when unchanged.
    new_station = model.raw.get("station_new")
    old_station = model.raw.get("station_raw", "")
    if new_station and new_station != old_station:
        text = re.sub(r'(stationAddress=")[^"]*(")',
                      lambda m: m.group(1) + new_station + m.group(2), text, count=1)
        old_ip = old_station[3:].strip() if old_station.startswith("IP ") else ""
        new_ip = new_station[3:].strip() if new_station.startswith("IP ") else ""
        if old_ip and new_ip:
            text = text.replace(f"&lt;{old_ip}&gt;", f"&lt;{new_ip}&gt;")

    sig_by_uid = {s.systemtag: s for s in
                  list(model.inp.signals) + list(model.out.signals)}

    if not model.raw.get("layout_dirty"):
        orig = model.raw.get("orig_names", {})
        def repl(m):
            block = m.group(0)
            u = re.search(r'systemTag=["\']([^"\']+)["\']', block)
            if u and u.group(1) in sig_by_uid:
                sig = sig_by_uid[u.group(1)]
                if sig.name != orig.get(u.group(1), sig.name):    # only if renamed
                    return _sub("displayName", sig.name, block)
            return block
        return re.sub(r"<Signal\b.*?</Signal>", repl, text, flags=re.S).encode("utf-8")

    # PROFINET: nested Slot/Subslot modules -> place by byte-range (accessPath
    # global, 6103 module-local). Same renderer as the SyCon detail; the Val3
    # signal body has no 6100 property, so _render_body's 6100 substitution is a
    # no-op. Signals outside modules (diagnostics / F-signals) stay byte-exact.
    if model.raw.get("protocol_kind") == "profinet":
        text = _render_by_modules(
            text, model,
            lambda b, s, base: _render_body(b, s, profinet=True, base_bits=base))
        return text.encode("utf-8")

    # layout changed: regenerate each DATA module's signal bodies in place. A
    # module holds one direction (EtherCAT RxPDO=input / TxPDO=output) or both in
    # order (EtherNet/IP single Connect module: inputs then outputs). Signals
    # OUTSIDE any module (adapter / diagnostics: Communication State, Watchdog, …)
    # are left byte-exact. The module boundary is implicit (one module per regex
    # match), so add/delete/reorder can't lose it.
    def render(sig, by_uid, by_dt):
        bo = sig.bit_offset or 0
        ws, t = by_uid.get(sig.systemtag) or by_dt.get(sig.sycon_dtype) \
            or next(iter(by_dt.values()))
        t = _sub("systemTag", sig.systemtag, t)
        t = _sub("displayName", sig.name, t)
        if sig.signal_type:
            t = _sub("signalType", sig.signal_type, t)
        t = _sub("signalAccessPath", _access_path(sig), t)
        t = _sub("dataType", sig.sycon_dtype, t)
        t = _sub("arrayElements", sig.array_elements, t)
        t = re.sub(r'(id=["\']6103["\'][^>]*value=["\'])[^"\']*(["\'])',
                   lambda x: x.group(1) + _off_b64(bo) + x.group(2), t, count=1)
        return ws + t

    def regen_module(mm):
        inner = re.search(r"(<Module\b[^>]*>)(.*)(</Module>)", mm.group(0), re.S)
        head, body, tail = inner.group(1), inner.group(2), inner.group(3)
        blocks = list(re.finditer(r"(\s*)(<Signal\b.*?</Signal>)", body, re.S))
        if not blocks:
            return mm.group(0)
        types = set()
        for b in blocks:
            st = re.search(r'signalType=["\']([^"\']+)["\']', b.group(2))
            if st:
                types.add(st.group(1))
        if types == {"input"}:
            sigs = list(model.inp.signals)
        elif types == {"output"}:
            sigs = list(model.out.signals)
        else:                                          # mixed (EtherNet/IP)
            sigs = list(model.inp.signals) + list(model.out.signals)
        by_uid, by_dt = {}, {}
        for b in blocks:
            uid = re.search(r'systemTag=["\']([^"\']+)["\']', b.group(2)).group(1)
            dt = re.search(r'dataType=["\'](\w+)["\']', b.group(2)).group(1)
            by_uid[uid] = (b.group(1), b.group(2))
            by_dt.setdefault(dt, (b.group(1), b.group(2)))
        pre = body[:blocks[0].start()]
        suf = body[blocks[-1].end():]
        return head + pre + "".join(render(s, by_uid, by_dt) for s in sigs) + suf + tail

    text = re.sub(r"<Module\b[^>]*>.*?</Module>", regen_module, text, flags=re.S)
    return text.encode("utf-8")


def write(model, paths) -> dict:
    """Write the three files. For a normal edit the existing files are the
    skeleton (size unchanged). For a SIZE change the model carries
    raw['eip_skeleton'] = a SyCon-saved EtherNet/IP project of the TARGET size;
    its size-bearing parts (OLE2 lead, CIP tail, length fields, configMD5, .nxd)
    are reused and the current signals are injected into it (repacked)."""
    if model.raw.get("eip_eis"):
        return _eip_eis_write(model, paths)     # NETX 51 RE/EIS structural write
    skel = model.raw.get("eip_skeleton")
    base = skel or paths
    out = {"sycon": write_sycon(model, base.sycon_xml), "val3": None, "nxd": None}
    if base.val3_xml:
        out["val3"] = write_val3(model, base.val3_xml)
    if base.nxd:                        # skeleton's .nxd already has target size
        out["nxd"] = base.nxd.read_bytes()
    return out


def _eip_eis_write(model, paths) -> dict:
    """Full structural write for EtherNet/IP NETX 51 RE/EIS (BLOB IS MASTER). Reconciles the
    signals in the blob's EISAdapterBasic XML to the model (add/delete/edit/reorder), mirrors
    them into the Val3 ProcessData, keeps the .nxd (constant) and configMD5 (= f(sizes),
    unchanged by signal edits). The .spj is metadata only. Signal edits within the assembly
    size touch only EISAdapterBasic — SyCon-safe."""
    from .. import blob_eip
    from pathlib import Path

    def desired(iface):
        return [(s.systemtag, s.name, s.sycon_dtype, s.array_elements) for s in iface.signals]
    din, dout = desired(model.inp), desired(model.out)
    in_sz, out_sz = model.inp.max_bytes, model.out.max_bytes   # the assembly budget
    out = {"sycon": None, "val3": None, "nxd": None}
    # 1) BLOB (master): rebuild both directions' signals in EISAdapterBasic, then set the
    #    assembly sizes (CIPConnectBasic) so SyCon shows the resized budget.
    xml = sycon.read_xml(paths.sycon_xml)
    blob = sycon.blob_from_xml(xml)
    blob = blob_eip.set_direction_signals(blob, "input", din)
    blob = blob_eip.set_direction_signals(blob, "output", dout)
    blob = blob_eip.set_assembly_sizes(blob, in_sz, out_sz)
    out["sycon"] = re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                          lambda m: m.group(1) + bytes(blob).hex().upper() + m.group(2),
                          xml, flags=re.S).encode("utf-8")
    # 2) Val3 export: mirror the signals into the ProcessData <Module> (same XML format),
    #    keep configMD5 (size-invariant for signal edits; SyCon-internal hash for resize)
    if paths.val3_xml:
        v = sycon.read_xml(paths.val3_xml)
        v = blob_eip.rebuild_module_signals(v, "input", din)
        v = blob_eip.rebuild_module_signals(v, "output", dout)
        out["val3"] = v.encode("utf-8")
    # 3) .nxd (what the robot reads): write the assembly sizes (@1141/@1181) + recompute the
    #    netX integrity MD5. THIS is how the robot gets the resized size.
    if paths.nxd:
        out["nxd"] = blob_eip.set_nxd_sizes(Path(paths.nxd).read_bytes(), in_sz, out_sz)
    return out


def generic_load(paths, protocol: str):
    """Shared reader for the modular detail protocols (EtherCAT, PROFINET): parse
    the signal blocks, split In = signalType 'input' / Out = 'output', size from
    the signal span. Device IP from the Val3 export if present. Editing is limited
    to rename (byte-exact); structural offset recompute is protocol-specific."""
    blob = sycon.blob_from_xml(sycon.read_xml(paths.sycon_xml))
    try:
        anchor, declen, detail = sycon.detail_block(blob)
    except ValueError:
        # an empty config (all modules deleted) has no signal-detail block — load it as
        # 0 signals instead of failing (else the GUI keeps a stale model after the last
        # delete and the next op acts on a module that's already gone).
        anchor, declen, detail = None, 0, b""
    is_pn = protocol == "PROFINET"
    pairs = parse_signals(detail, profinet=is_pn) if detail else []
    in_sigs = [s for s, t in pairs if t == "input"]
    out_sigs = [s for s, t in pairs if t == "output"]
    dev = DeviceInfo(protocol=protocol, base_name=paths.base_name)
    sinfo = _station_info(paths, protocol, dev)
    # total data size per direction = sum of signal widths (handles multi-module:
    # EtherCAT one PDO/dir, PROFINET several slots whose offsets restart per slot)
    in_bytes = (sum(s.bits for s in in_sigs) + 7) // 8
    out_bytes = (sum(s.bits for s in out_sigs) + 7) // 8
    # EtherCAT is flat (continuous per-direction offsets like EtherNet/IP) AND its
    # .nxd carries the FIXED process-image size -> add/delete/reorder work within
    # that image, byte-exact, with the .nxd untouched. PROFINET stays rename-only
    # (nested per-slot accessPath the flat model can't reproduce).
    in_max, out_max = in_bytes, out_bytes
    # RENAME-ONLY (SyCon-safe). Add/Delete/Reorder/Resize change the detail length,
    # but the device's PDO/process-image config lives in the LARGE BINARY part of
    # the SyCon blob (internal records + offsets) which this writer does not yet
    # rebuild — so a structural edit produces a project SyCon refuses to load
    # ("Gerät kann nicht erzeugt werden"). Until the binary part is reconstructed
    # (see docs/08 + task B), these protocols expose rename + general only. The
    # fixed image SIZE is still read (for display / future structural support).
    structural = reorderable = resizable = False
    pn_modules = []
    if protocol == "PROFINET" and detail:
        pn_modules = _pn_module_ranges(detail)   # (direction, byte_start, byte_size)
        pin = sum(sz for d, st, sz in pn_modules if d == "input")
        pout = sum(sz for d, st, sz in pn_modules if d == "output")
        if pin or pout:
            in_max = max(in_bytes, pin)          # fixed image = sum of module sizes
            out_max = max(out_bytes, pout)
    elif protocol == "EtherCAT":
        ec_in, ec_out = _ethercat_nxd_sizes(paths)
        if ec_in is not None:
            in_max = max(in_bytes, ec_in)        # fixed image size from the .nxd
            out_max = max(out_bytes, ec_out)
        # validated structural compiler (blob_ec): per-signal add/delete/edit/reorder +
        # resize are SyCon-safe (delete + add confirmed open/save in SyCon.net 2026-06-16).
        structural = reorderable = resizable = True
    inp = Interface("In", in_max, signals=in_sigs)
    out = Interface("Out", out_max, signals=out_sigs)
    model = ConfigModel(dev, inp, out, raw={
        "protocol_kind": protocol.lower(),
        "bit_addressed": True,
        "modular": True,            # multi-module detail
        "structural": structural,   # add / delete
        "reorderable": reorderable, # drag-to-reorder (needs repack-style move)
        "resizable": resizable,     # resize via a target-size skeleton
        "paths": paths,
        "detail_anchor": anchor,
        "orig_names": {s.systemtag: s.name for s in in_sigs + out_sigs},
        "pn_modules": pn_modules,   # PROFINET leaf-module byte ranges (add/free-slot)
        **sinfo,
    })
    nx = _nxd_info(paths)
    if nx:
        model.raw["nxd"] = {"size": nx["size"], "md5_ok": nx["md5_ok"]}
    # PROFINET scalar device settings (Stufe 1): startup + watchdog from the main
    # .nxd, endian from the Val3 byteOrder. Stored in raw for the General editor.
    if protocol == "PROFINET":
        from .. import nxd_pn
        # BLOB is the MASTER: read scalar device settings + name from the blob; fall back
        # to the exports (.nxd / _nwid / Val3) only when the blob can't be read.
        ds = _pn_blob_read_devsettings(blob)
        if ds is not None:
            model.raw["pn_startup"] = ds["startup"]
            model.raw["pn_watchdog"] = ds["watchdog"]
            model.raw["pn_endian_big"] = ds["endian_big"]
            if "io_state" in ds:                 # IO State Information (Producer), read-only
                model.raw["pn_io_state"] = ds["io_state"]
        else:
            if paths.nxd and paths.nxd.is_file():
                ch = nxd_pn.read_channel(paths.nxd.read_bytes())
                if ch is not None:
                    model.raw["pn_startup"], model.raw["pn_watchdog"] = ch
            model.raw["pn_endian_big"] = (sinfo.get("byte_order", "big") != "little")
        nm = _pn_blob_read_name(blob)
        if not nm and paths.nwid_nxd and paths.nwid_nxd.is_file():
            nm = nxd_pn.read_station_name(paths.nwid_nxd.read_bytes())
        if nm:
            dev.node_name = nm                   # authoritative station name
            model.raw["station_fields"] = {"kind": "name", "name": nm}
        model.raw["pn_orig_name"] = dev.node_name or ""   # for text-replace on write
        # detailed module map (slot, size, direction, global byte start, signals) for the
        # GUI's module bands + slot-bounded signal placement.
        try:
            from .. import blob_pn
            model.raw["pn_module_list"] = blob_pn.parse_modules(
                sycon.read_xml(paths.sycon_xml))
        except Exception:
            model.raw["pn_module_list"] = []
    # EtherCAT "General Settings" scalars (Stufe 1): read from the export .nxd.
    # EtherCAT-only path -> PROFINET/EIP/POWERLINK untouched.
    if protocol == "EtherCAT":
        # The SyCon project BLOB is the MASTER (the .nxd is only an export and can be
        # stale). Read the General Settings from the blob; fall back to the .nxd only if
        # the blob can't be read.
        g = None
        if paths.sycon_xml and paths.sycon_xml.is_file():
            g = _ethercat_blob_read_general(
                paths.sycon_xml.read_text(encoding="utf-8", errors="replace"))
        if g is None and paths.nxd and paths.nxd.is_file():
            from .. import nxd_ec
            g = nxd_ec.read_general(paths.nxd.read_bytes())
            if g is not None and paths.sycon_xml and paths.sycon_xml.is_file():
                ios = _ethercat_blob_read_iostatus(
                    paths.sycon_xml.read_text(encoding="utf-8", errors="replace"))
                if ios is not None:
                    g["io_data_status"] = ios
        if g is not None:
            model.raw["ec_general"] = g
    return model


def _pn_blob_set_name(xml_text: str, new_name: str):
    """Set the PROFINET device name in the SyCon project blob (STRUCTURAL — reads the
    current value from each spot, no dependence on a tracked old name, so repeated
    renames and any drift stay in sync):
      • `deviceNo="..."` (plain text, outside <BinData>)
      • the device-instance name in the CFB stream `PNIODeviceDataModelBasic`. There
        the name is a length-prefixed field at the stream start: header[0:6],
        u32 byte-length @6 (= name_chars*2 + 2 for the NUL), utf-16 name @10. We set
        the length field TIGHT (what SyCon needs — a non-tight length makes SyCon throw
        an internal error) and keep the rest of the stream unchanged (incl. its GUIDs),
        zero-padding the tail so the stream stays the SAME byte size -> olefile writes
        it back in place, the OLE2/CFB stays byte-structurally intact (verified). No
        guessing, no hand-rolled CFB resize.
    Returns (new_xml, ok). ok=False (deviceNo only) if the new name does NOT fit the
    stream's existing size (longer than the original) — growing the CFB stream would
    need a real resize, deferred; the robot still gets the name via _nwid.nxd + Val3."""
    import io as _io
    import struct as _st
    import olefile as _olefile
    from .. import cfb_write as _cfbw
    # deviceNo: replace the CURRENT value (whatever it is) -> structural, not old_name
    new_xml = re.sub(r'(deviceNo=")[^"]*(")',
                     lambda m: m.group(1) + new_name + m.group(2), xml_text, count=1)

    def _replace_name_at(data, loff, noff):
        """Replace the length-prefixed utf-16 name buffer at a FIXED offset (loff=u32 byte
        length, noff=name start, noff==loff+4). Located by POSITION not by the current
        value, so it works even when the streams' names have drifted from deviceNo.
        Returns new bytes, or None if the field doesn't validate."""
        if noff != loff + 4 or noff + 4 > len(data):
            return None
        L = _st.unpack_from("<I", data, loff)[0]
        if not (2 <= L <= 256 and L % 2 == 0 and noff + L <= len(data)
                and data[noff + L - 2:noff + L] == b"\x00\x00"):
            return None
        nf = new_name.encode("utf-16-le") + b"\x00\x00"
        return data[:loff] + _st.pack("<I", len(nf)) + nf + data[noff + L:]

    blob = sycon.blob_from_xml(xml_text)
    try:
        ole = _olefile.OleFileIO(_io.BytesIO(blob[4:]))
        tree = _cfbw.read_tree(ole)
        ole.close()
    except Exception:
        return new_xml, False
    # the station name lives in TWO CFB streams, each as a length-prefixed buffer at a
    # fixed offset: PNIODeviceDataModelBasic (len@6/name@10 — the signal-config view) AND
    # PNIODTMBASE (len@36/name@40 — SyCon's "Name of station" in General; missing this
    # made the name revert when the dialog opened). Update BOTH; rebuild via cfb_write so
    # it works whatever the stream size (the device stream grows past 4096 with modules).
    ok = False
    for stream, loff, noff in (("PNIODeviceDataModelBasic", 6, 10),
                               ("PNIODTMBASE", 36, 40)):
        if isinstance(tree.get(stream), (bytes, bytearray)):
            nd = _replace_name_at(bytes(tree[stream]), loff, noff)
            if nd is not None:
                tree[stream] = nd
                ok = ok or stream == "PNIODeviceDataModelBasic"
    if not ok:
        return new_xml, False
    cfb = _cfbw.build(tree)
    hexlo = (_st.pack("<I", len(cfb)) + cfb).hex()
    new_xml = re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                     lambda m: m.group(1) + hexlo + m.group(2), new_xml, flags=re.S)
    return new_xml, True


def _pn_blob_rename_signals(xml_text: str, renames):
    """I/O LABELING — rename signal(s) inside existing PROFINET modules in the SyCon
    blob. PROVEN by a clean SyCon A/B diff (signal "BBB1" vs "AAA1", both SyCon-saved
    and valid): a rename changes EXACTLY ONE content spot — `displayName="<name>"` in
    the embedded device XML of CFB stream `PNIODeviceDataModelBasic` (utf-16, NOT
    length-prefixed — the XML runs to the stream end). SyCon does NOT touch the
    per-signal `PNIODataBasic` name field (it keeps the module's original data name,
    e.g. "Eingänge") nor main.nxd / configMD5 / _nwid. (The ~60 other 4-byte deltas in
    the A/B diff are FILETIME save timestamps — `[X][c0fbdc01]` ≈ 2026 — pure save
    noise, not content; we edit in place and leave them untouched.) `renames` = list of
    (old, new). Returns (new_xml, ok)."""
    import io as _io
    import struct as _st
    import olefile as _olefile
    from .. import cfb as _cfb
    renames = [(o, n) for o, n in renames if o and n and o != n]
    if not renames:
        return xml_text, True
    blob = sycon.blob_from_xml(xml_text)
    cfbytes = blob[4:]
    S = "PNIODeviceDataModelBasic"
    try:
        ole = _olefile.OleFileIO(_io.BytesIO(cfbytes))
        if not ole.exists(S):
            ole.close(); return xml_text, False
        data = ole.openstream(S).read()
        ole.close()
        orig_len = len(data)
        # locate the embedded-XML length field: a u32 == the byte length of the device
        # XML that immediately follows it (the XML runs to the stream end), followed by
        # '<'. SyCon updates THIS when the label length changes; not updating it makes
        # SyCon read past the XML end -> "Signalkonfiguration: unerwarteter Fehler".
        lenfield = None
        for i in range(orig_len - 6):
            if data[i + 4:i + 6] == b"<\x00" and \
                    _st.unpack_from("<I", data, i)[0] == orig_len - (i + 4):
                lenfield = i
                break
        for old, new in renames:
            a = f'displayName="{old}"'.encode("utf-16-le")
            b = f'displayName="{new}"'.encode("utf-16-le")
            if data.count(a) != 1:                # must be the unique signal label
                return xml_text, False
            data = data.replace(a, b, 1)
        delta = len(data) - orig_len
        if delta:                                 # length changed -> fix the XML length field
            if lenfield is None:
                return xml_text, False            # don't emit a file SyCon will reject
            data = bytearray(data)
            _st.pack_into("<I", data, lenfield, len(data) - (lenfield + 4))
            data = bytes(data)
        cfbytes = _cfb.resize_stream(cfbytes, S, data)   # same/length-changing
        if cfbytes is None:
            return xml_text, False
    except Exception:
        return xml_text, False
    hexlo = (blob[:4] + cfbytes).hex()
    new_xml = re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                     lambda m: m.group(1) + hexlo + m.group(2), xml_text, flags=re.S)
    return new_xml, True


def _pn_blob_read_devsettings(blob: bytes):
    """Read PROFINET scalar device settings from the blob `PNIO_DeviceSettings` stream
    (the MASTER): startup byte@2, watchdog u32@8, endian byte@14 (0=big, 1=little).
    Returns {startup, watchdog, endian_big} or None. `blob` = the length-prefixed blob."""
    import io as _io
    import struct as _st
    import olefile
    try:
        ole = olefile.OleFileIO(_io.BytesIO(blob[4:]))
        if not ole.exists("PNIO_DeviceSettings"):
            return None
        ds = ole.openstream("PNIO_DeviceSettings").read()
        if len(ds) < 15:
            return None
        r = {"startup": ds[2], "watchdog": _st.unpack_from("<I", ds, 8)[0],
             "endian_big": ds[14] == 0}
        if len(ds) > 32:
            r["io_state"] = ds[32]      # IO State Information enum (0=Disabled,1=Bit,2=Byte)
        return r
    except Exception:
        return None


def _pn_blob_read_name(blob: bytes):
    """Read the PROFINET device/station name from the blob `PNIODeviceDataModelBasic`
    stream (the MASTER): u32 byte-length @6, utf-16 name @10. Returns the name or None."""
    import io as _io
    import struct as _st
    import olefile
    try:
        ole = olefile.OleFileIO(_io.BytesIO(blob[4:]))
        if not ole.exists("PNIODeviceDataModelBasic"):
            return None
        d = ole.openstream("PNIODeviceDataModelBasic").read()
        ln = _st.unpack_from("<I", d, 6)[0]
        if ln <= 0 or 10 + ln > len(d):
            return None
        return d[10:10 + ln].decode("utf-16-le", "replace").rstrip("\x00") or None
    except Exception:
        return None


def _eip_blob_read_ip(blob: bytes):
    """EtherNet/IP: read the adapter IP from the blob (the MASTER) — CFB stream
    `CahedAdapter/ENIPIpSettings/ENIPIpSettingsBasic` (u32 byte-length @6, utf-16 IP
    string @10; note Hilscher's stream-name typo 'Cahed'). Returns the IP or None."""
    import io as _io
    import struct as _st
    import olefile
    try:
        ole = olefile.OleFileIO(_io.BytesIO(blob[4:]))
        p = "CahedAdapter/ENIPIpSettings/ENIPIpSettingsBasic"
        if not ole.exists(p):
            return None
        d = ole.openstream(p).read()
        ln = _st.unpack_from("<I", d, 6)[0]
        if ln <= 0 or 10 + ln > len(d):
            return None
        return d[10:10 + ln].decode("utf-16-le", "replace").rstrip("\x00") or None
    except Exception:
        return None


def _pn_blob_patch_devsettings(xml_text: str, off: int, fmt: str, value) -> str:
    """Patch a fixed-size field in the blob's `PNIO_DeviceSettings` CFB stream (42 B,
    SAME size -> olefile writes it in place, no resize). Used for watchdog (u32 @8)
    and later startup/endian once their byte offsets are verified by a clean diff."""
    import io as _io
    import struct as _st
    import olefile as _olefile
    blob = sycon.blob_from_xml(xml_text)
    try:
        bio = _io.BytesIO(blob[4:])
        ole = _olefile.OleFileIO(bio, write_mode=True)
        if not ole.exists("PNIO_DeviceSettings"):
            ole.close(); return xml_text
        d = bytearray(ole.openstream("PNIO_DeviceSettings").read())
        if off + _st.calcsize(fmt) > len(d):
            ole.close(); return xml_text
        _st.pack_into(fmt, d, off, value)
        ole.write_stream("PNIO_DeviceSettings", bytes(d))
        ole.close()
    except Exception:
        return xml_text
    hexlo = (blob[:4] + bio.getvalue()).hex()
    return re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                  lambda m: m.group(1) + hexlo + m.group(2), xml_text, flags=re.S)


def _pn_scalar_write(model, paths) -> dict:
    """PROFINET Stufe-1 scalar write — STATION NAME, set STRUCTURALLY in every spot it
    lives so all stay in sync (no dependence on a tracked old name; repairs any drift):
      • _nwid.nxd : the name buffer + MD5 (byte-exact reproduction of SyCon, proven)
      • Val3      : stationAddress = "Addr <name>" + the displayName ...&lt;name&gt;
      • blob      : deviceNo="<name>" + the CFB device-record name (PNIODeviceDataModelBasic)
    main.nxd is not involved in a rename; configMD5 is NOT in the blob, so neither is
    touched. No detail rebuild -> OLE2/CFB stays intact. The CFB device-record name is
    only set when the new name FITS the existing stream size (<= original); a longer
    name updates the robot files + deviceNo only (pn_name_cfb_ok=False) — growing the
    CFB stream needs a real resize (deferred). Watchdog/startup/endian: separate diffs."""
    from .. import nxd_pn
    out = {"sycon": None, "val3": None, "nxd": None, "nwid": None}
    name = (model.device.node_name or "").strip()
    if not name:
        return out

    if paths.nwid_nxd and paths.nwid_nxd.is_file():
        out["nwid"] = nxd_pn.patch_station_name(paths.nwid_nxd.read_bytes(), name)

    if paths.val3_xml and paths.val3_xml.is_file():
        t = paths.val3_xml.read_text(encoding="utf-8", errors="replace")
        cur = re.search(r'stationAddress="(?:Add?r )?([^"]*)"', t)   # strip Addr/Adr
        t = re.sub(r'(stationAddress=")[^"]*(")',
                   lambda m: m.group(1) + f"Addr {name}" + m.group(2), t, count=1)
        if cur and cur.group(1) and cur.group(1) != name:
            t = t.replace(f"&lt;{cur.group(1)}&gt;", f"&lt;{name}&gt;")   # displayName
        out["val3"] = t.encode("utf-8")

    blob_txt = None
    if paths.sycon_xml and paths.sycon_xml.is_file():
        blob_txt, ok = _pn_blob_set_name(
            paths.sycon_xml.read_text(encoding="utf-8", errors="replace"), name)
        model.raw["pn_name_cfb_ok"] = ok       # False => name too long for the CFB buffer
    model.raw["pn_orig_name"] = name

    # --- I/O LABELING: rename signal(s) inside existing modules. The label lives in
    # the blob (PNIODeviceDataModelBasic displayName) + Val3 displayName only; main.nxd
    # is untouched (no recompile, no configMD5 change). Renames come from the original
    # names captured at load (orig_names, keyed by the systemTag that travels with the
    # signal) vs the current model names. ---
    orig = model.raw.get("orig_names", {})
    renames = []
    for sig in model.inp.signals + model.out.signals:
        old = orig.get(sig.systemtag)
        if old and old != sig.name:
            renames.append((old, sig.name))
    if renames:
        if blob_txt is not None:
            blob_txt, sig_ok = _pn_blob_rename_signals(blob_txt, renames)
            model.raw["pn_label_blob_ok"] = sig_ok
        v = (out["val3"].decode("utf-8") if out["val3"] is not None else
             (paths.val3_xml.read_text(encoding="utf-8", errors="replace")
              if paths.val3_xml and paths.val3_xml.is_file() else None))
        if v is not None:
            for old, new in renames:
                v = v.replace(f'displayName="{old}"', f'displayName="{new}"', 1)
            out["val3"] = v.encode("utf-8")
        # keep the load-time map current so a second rename in the same session works
        model.raw["orig_names"] = {s.systemtag: s.name
                                   for s in model.inp.signals + model.out.signals}

    # --- Startup + Watchdog + Endian (all verified byte-exact vs SyCon, all scalar):
    # main.nxd CHANNEL block (startup byte + watchdog u32) / per-submodule endian flags
    # + blob PNIO_DeviceSettings (startup byte @2, watchdog u32 @8, endian byte @14:
    # 0=big/1=little) + Val3 (byteOrder + configMD5 = new main.nxd MD5). ---
    su = model.raw.get("pn_startup")
    wd = model.raw.get("pn_watchdog")
    big = model.raw.get("pn_endian_big")
    # --- main.nxd export: SELF-HEAL startup/watchdog/endian to the desired values
    # (diff vs the .nxd; it may be stale -> brings it up to `desired`). Val3 configMD5
    # follows the new .nxd MD5. ---
    if paths.nxd and paths.nxd.is_file():
        orig_nxd = paths.nxd.read_bytes()
        new_nxd = orig_nxd
        cur = nxd_pn.read_channel(orig_nxd)
        su_chg = su is not None and cur is not None and cur[0] != (1 if su else 0)
        wd_chg = wd is not None and cur is not None and cur[1] != int(wd)
        if (su_chg or wd_chg) and cur is not None:
            new_nxd = nxd_pn.patch_channel(new_nxd,
                                           (1 if su else 0) if su is not None else cur[0],
                                           int(wd) if wd is not None else cur[1])
        if big is not None and nxd_pn.read_endian_big(new_nxd) != bool(big):
            new_nxd = nxd_pn.patch_endian(new_nxd, bool(big))
        if new_nxd != orig_nxd:
            out["nxd"] = new_nxd
            old_md5, new_md5 = nxd_pn.md5_hex(orig_nxd), nxd_pn.md5_hex(new_nxd)
            v = (out["val3"].decode("utf-8") if out["val3"] is not None else
                 (paths.val3_xml.read_text(encoding="utf-8", errors="replace")
                  if paths.val3_xml and paths.val3_xml.is_file() else None))
            if v is not None and old_md5 != new_md5:
                out["val3"] = v.replace(f'configMD5="{old_md5}"',
                                        f'configMD5="{new_md5}"').encode("utf-8")
    # --- blob PNIO_DeviceSettings: BLOB IS MASTER -> diff vs the BLOB's own values,
    # independent of the .nxd (a stale .nxd whose value == desired must NOT skip the
    # blob write — the watchdog/sync-class bug). startup@2 / watchdog@8 / endian@14. ---
    if blob_txt is not None:
        cb = _pn_blob_read_devsettings(sycon.blob_from_xml(blob_txt))
        if cb is not None:
            if su is not None and (1 if su else 0) != cb["startup"]:
                blob_txt = _pn_blob_patch_devsettings(blob_txt, 2, "<B", 1 if su else 0)
            if wd is not None and int(wd) != cb["watchdog"]:
                blob_txt = _pn_blob_patch_devsettings(blob_txt, 8, "<I", int(wd))
            if big is not None and bool(big) != cb["endian_big"]:
                blob_txt = _pn_blob_patch_devsettings(blob_txt, 14, "<B", 0 if big else 1)
    # --- Val3 byteOrder export: reflect the desired endian (diff vs current) ---
    if big is not None:
        v = (out["val3"].decode("utf-8") if out["val3"] is not None else
             (paths.val3_xml.read_text(encoding="utf-8", errors="replace")
              if paths.val3_xml and paths.val3_xml.is_file() else None))
        if v is not None:
            want = "big" if big else "little"
            cbo = re.search(r'byteOrder="([^"]*)"', v)
            if cbo and cbo.group(1) != want:
                out["val3"] = re.sub(r'(byteOrder=")[^"]*(")',
                                     lambda m: m.group(1) + want + m.group(2),
                                     v, count=1).encode("utf-8")

    if blob_txt is not None:
        out["sycon"] = blob_txt.encode("utf-8")
    return out


def _ethercat_structural_write(model, paths) -> dict:
    """Full EtherCAT structural write (BLOB IS MASTER). Reconciles the blob's signals to the
    model's desired list per direction (add/delete/edit/reorder/resize, systemTag travels)
    via blob_ec.set_direction_signals, mirrors it into the export .nxd, applies any changed
    General Settings, and refreshes the Val3 export (signal labels + configMD5 = new .nxd
    MD5). The .spj is metadata only and untouched (SyCon reads _S129/SYCON_net.xml)."""
    import hashlib
    from pathlib import Path
    from .. import blob_ec
    def desired(iface):
        # EtherCAT GUIDs are upper-case in the blob/.nxd; the systemTag travels with the
        # signal (preserved on edit/reorder, fresh only for a newly added signal).
        return [(s.systemtag.upper(), s.name, s.sycon_dtype, s.array_elements)
                for s in iface.signals]
    din, dout = desired(model.inp), desired(model.out)
    in_sz, out_sz = model.inp.max_bytes, model.out.max_bytes   # configured size (may be > used)
    out = {"sycon": None, "val3": None, "nxd": None}
    # 1) BLOB (master): rebuild both directions (SM length = configured size, trailing free
    #    bytes kept), write back into <BinData>
    xml = sycon.read_xml(paths.sycon_xml)
    blob = sycon.blob_from_xml(xml)
    blob = blob_ec.set_direction_signals(blob, "input", din, size_bytes=in_sz)
    blob = blob_ec.set_direction_signals(blob, "output", dout, size_bytes=out_sz)
    # 2) export .nxd: same reconcile, then stamp the configured process-image size
    if paths.nxd:
        import struct as _st
        nxd = Path(paths.nxd).read_bytes()
        nxd = blob_ec.set_direction_signals_nxd(nxd, "input", din)
        nxd = blob_ec.set_direction_signals_nxd(nxd, "output", dout)
        nxd = bytearray(nxd)
        _st.pack_into("<H", nxd, 380, in_sz)       # In  process-image size (>= used)
        _st.pack_into("<H", nxd, 408, out_sz)      # Out process-image size (>= used)
        nxd[0x54:0x54 + 16] = hashlib.md5(bytes(nxd[136:])).digest()
        out["nxd"] = bytes(nxd)
    out["sycon"] = re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                          lambda m: m.group(1) + bytes(blob).hex().upper() + m.group(2),
                          xml, flags=re.S).encode("utf-8")
    # 3) General Settings (in-place on the rebuilt blob/.nxd) — EtherCAT-only
    out = _ethercat_apply_general(model, out)
    # 4) Val3 export: signal labels + configMD5 = MD5 of the final .nxd data
    if paths.val3_xml:
        out["val3"] = write_val3(model, paths.val3_xml)
        if out.get("nxd") is not None:
            new_md5 = hashlib.md5(out["nxd"][136:]).hexdigest().upper()
            v = out["val3"].decode("utf-8", "replace")
            out["val3"] = re.sub(r'configMD5="[^"]*"', f'configMD5="{new_md5}"',
                                 v).encode("utf-8")
    return out


def generic_write(model, paths) -> dict:
    # For a SIZE change the model carries raw['ec_skeleton'] = a SyCon-saved
    # EtherCAT project of the TARGET size; its size-bearing parts (OLE2 lead, CIP
    # tail, .nxd of the new size, module skeleton) are reused and the current
    # signals are injected (re-packed). Otherwise the existing files are the
    # skeleton (size unchanged) and the .nxd is copied verbatim.
    if model.raw.get("protocol_kind") == "profinet":
        return _pn_scalar_write(model, paths)
    if model.raw.get("protocol_kind") == "ethercat":
        return _ethercat_structural_write(model, paths)
    skel = model.raw.get("ec_skeleton") or model.raw.get("pn_skeleton")
    base = skel or paths
    out = {"sycon": write_sycon(model, base.sycon_xml), "val3": None, "nxd": None}
    if base.val3_xml:
        out["val3"] = write_val3(model, base.val3_xml)
    if base.nxd:
        if model.raw.get("protocol_kind") == "ethercat":
            # EtherCAT: the process-image size lives in the .nxd (@380/@408). Patch
            # it to the current max (+ MD5) so resize is a simple byte-count change,
            # no skeleton needed. A no-op resize reproduces the file byte-exact.
            out["nxd"] = _ethercat_nxd_resized(base.nxd, model.inp.max_bytes,
                                               model.out.max_bytes)
            out = _ethercat_apply_general(model, out)   # General Settings (Stufe 1)
        else:
            out["nxd"] = base.nxd.read_bytes()
    return out


def _ethercat_apply_general(model, out) -> dict:
    """Apply changed EtherCAT "General Settings" to the outputs. EtherCAT-only — never
    touches PROFINET/EIP/POWERLINK. Desired values live in model.raw['ec_general'].
    Two classes of field:
      • .nxd fields (startup/watchdog/vendor/product/revision/serial/sync/alias): patched
        byte-exact in out['nxd'] + the new configMD5 into out['val3'] + the blob.
      • io_data_status: BLOB-ONLY (no .nxd representation -> no robot effect via the
        export) -> patched only in the SyCon project blob (ECTDeviceBasic @262)."""
    from .. import nxd_ec
    desired = model.raw.get("ec_general")
    nxd = out.get("nxd")
    if not desired or not nxd:
        return out
    cur = nxd_ec.read_general(nxd)
    if cur is None:
        return out
    # .nxd: patch fields that differ from the .nxd (stale .nxd self-heals to `desired`).
    nxd_changed = {k: desired[k] for k in cur if k in desired and desired[k] != cur[k]}
    # BLOB is the MASTER -> diff the blob fields against the BLOB's OWN current values,
    # NOT against the .nxd (the .nxd can be stale, so diffing vs it misses a field whose
    # desired value happens to equal the stale .nxd but differs from the blob — exactly
    # what dropped watchdog/sync from the blob write).
    blob_changed = {}
    if out.get("sycon") is not None:
        cur_blob = _ethercat_blob_read_general(out["sycon"].decode("utf-8", "replace")) or {}
        for k in ("bus_startup", "watchdog_ms", "station_alias",
                  "io_data_status", "sync_x10ns"):
            if k in desired and desired.get(k) != cur_blob.get(k):
                blob_changed[k] = desired[k]
    if not nxd_changed and not blob_changed:
        return out
    if nxd_changed:
        old_md5 = nxd_ec.md5_hex(nxd)
        out["nxd"] = nxd_ec.patch_general(nxd, nxd_changed)
        new_md5 = nxd_ec.md5_hex(out["nxd"])
        if out.get("val3") is not None and old_md5 != new_md5:
            out["val3"] = out["val3"].decode("utf-8", "replace").replace(
                f'configMD5="{old_md5}"', f'configMD5="{new_md5}"').encode("utf-8")
    if blob_changed and out.get("sycon") is not None:
        try:
            out["sycon"] = _ethercat_blob_patch_general(
                out["sycon"].decode("utf-8", "replace"), blob_changed).encode("utf-8")
        except Exception:
            pass        # blob stays identity; robot uses the .nxd regardless
    return out


def _ethercat_blob_read_general(xml_text: str):
    """Read ALL EtherCAT General Settings from the SyCon project blob — the MASTER
    (what SyCon edits and treats as authoritative; the .nxd is only an export that can
    go stale). Scalars from `CachedSlave/ECTDeviceBasic`; ident from the DeviceIdentity
    streams (Vendor/ECTVendorBasic @8, ECTDeviceIdentityBasic product @8 / revision @16).
    Returns dict (same keys as nxd_ec.read_general + io_data_status) or None."""
    import io as _io
    import struct as _struct
    from .. import cfb_write
    import olefile
    m = re.search(r"<BinData[^>]*>([0-9A-Fa-f\s]+)</BinData>", xml_text)
    if not m:
        return None
    try:
        cfb = bytes.fromhex("".join(m.group(1).split()))[4:]
        T = cfb_write.read_tree(olefile.OleFileIO(_io.BytesIO(cfb)))
    except Exception:
        return None
    cs = T.get("CachedSlave")
    dev = cs.get("ECTDeviceBasic") if isinstance(cs, dict) else None
    if not isinstance(dev, (bytes, bytearray)) or len(dev) < 280:
        return None
    di = cs.get("DeviceIdentity") if isinstance(cs, dict) else None
    di = di if isinstance(di, dict) else {}
    idb = di.get("ECTDeviceIdentityBasic")
    vnd = (di.get("Vendor") or {}).get("ECTVendorBasic") if isinstance(di.get("Vendor"), dict) else None
    u32 = lambda b, o: _struct.unpack_from("<I", b, o)[0]
    return {
        "bus_startup":    u32(dev, 238),
        "watchdog_ms":    u32(dev, 246),
        "station_alias":  u32(dev, 254),
        "io_data_status": u32(dev, 262),
        "sync_x10ns":     _struct.unpack_from("<H", dev, 276)[0],
        "vendor_id":      u32(vnd, 8) if isinstance(vnd, (bytes, bytearray)) and len(vnd) >= 12 else 0,
        "product_code":   u32(idb, 8) if isinstance(idb, (bytes, bytearray)) and len(idb) >= 12 else 0,
        "revision":       u32(idb, 16) if isinstance(idb, (bytes, bytearray)) and len(idb) >= 20 else 0,
        "serial":         0,
    }


def _ethercat_blob_read_iostatus(xml_text: str):
    """Read I/O Data Status (blob-only, ECTDeviceBasic @262) from the SyCon project
    blob, or None. 0 = None; nonzero = a status mode."""
    import io as _io
    import struct as _struct
    from .. import cfb_write
    import olefile
    m = re.search(r"<BinData[^>]*>([0-9A-Fa-f\s]+)</BinData>", xml_text)
    if not m:
        return None
    try:
        cfb = bytes.fromhex("".join(m.group(1).split()))[4:]
        tree = cfb_write.read_tree(olefile.OleFileIO(_io.BytesIO(cfb)))
        dev = tree.get("CachedSlave", {}).get("ECTDeviceBasic")
        if isinstance(dev, (bytes, bytearray)) and len(dev) >= 266:
            return _struct.unpack_from("<I", dev, 262)[0]
    except Exception:
        return None
    return None


def _ethercat_blob_patch_general(xml_text: str, changed: dict) -> str:
    """Patch the EtherCAT General-Settings scalars in the SyCon project blob IN PLACE,
    so re-opening in SyCon shows the new values. The settings live in the CFB stream
    `CachedSlave/ECTDeviceBasic` (startup u32@238, watchdog u32@246, alias u32@254,
    I/O Data Status u32@262, sync u16@276); `ECATDataModelBasic`[90] -> 9 (the 'model
    edited' marker). We patch the SAME-SIZE bytes directly in the raw CFB (located via
    the stream content, which is contiguous + unique) — NO cfb rebuild — so the blob
    stays BYTE-IDENTICAL to SyCon's original except those few setting bytes (SyCon
    accepts it for sure). Returns the input unchanged if the blob/stream isn't found."""
    import io as _io
    import struct as _struct
    import olefile
    m = re.search(r"(<BinData[^>]*>)([0-9A-Fa-f\s]+)(</BinData>)", xml_text)
    if not m:
        return xml_text
    raw = bytearray(bytes.fromhex("".join(m.group(2).split())))
    cfb = bytes(raw[4:])                            # raw[:4] = u32 CFB byte length
    try:
        ole = olefile.OleFileIO(_io.BytesIO(cfb))
        dev = ole.openstream("CachedSlave/ECTDeviceBasic").read()
        dm = ole.openstream("ECATDataModelBasic").read()
    except Exception:
        return xml_text
    if len(dev) < 280:
        return xml_text
    # locate ECTDeviceBasic content contiguously + uniquely in the raw CFB
    dpos = cfb.find(dev[:64])
    if dpos < 0 or cfb.count(dev[:64]) != 1 or cfb[dpos:dpos + len(dev)] != dev:
        return xml_text                            # not safely locatable -> bail
    base = 4 + dpos                                 # offset into `raw`
    def w32(off, v): _struct.pack_into("<I", raw, base + off, int(v) & 0xFFFFFFFF)
    def w16(off, v): _struct.pack_into("<H", raw, base + off, int(v) & 0xFFFF)
    if "bus_startup" in changed:    w32(238, 1 if changed["bus_startup"] else 0)
    if "watchdog_ms" in changed:    w32(246, changed["watchdog_ms"])
    if "station_alias" in changed:  w32(254, changed["station_alias"])
    if "io_data_status" in changed: w32(262, changed["io_data_status"])
    if "sync_x10ns" in changed:     w16(276, changed["sync_x10ns"])
    # ECATDataModelBasic[90] = 9 (edited marker), also in place
    mpos = cfb.find(dm[:40])
    if len(dm) > 90 and mpos >= 0 and cfb.count(dm[:40]) == 1 and cfb[mpos:mpos + len(dm)] == dm:
        raw[4 + mpos + 90] = 9
    new_hex = bytes(raw).hex()                      # same length -> u32 prefix unchanged
    if any(c in "ABCDEF" for c in m.group(2)):
        new_hex = new_hex.upper()
    return xml_text[:m.start(2)] + new_hex + xml_text[m.end(2):]


def ec_skeleton_sizes(skeleton_paths) -> tuple[int, int]:
    """(in_bytes, out_bytes) fixed process-image size of an EtherCAT skeleton —
    read straight from its .nxd (@380/@408)."""
    ec_in, ec_out = _ethercat_nxd_sizes(skeleton_paths)
    if ec_in is not None:
        return ec_in, ec_out
    m = generic_load(skeleton_paths, "EtherCAT")
    return m.inp.max_bytes, m.out.max_bytes


def pn_skeleton_sizes(skeleton_paths) -> tuple[int, int]:
    """(in_bytes, out_bytes) fixed image of a PROFINET skeleton = sum of its
    module sizes per direction."""
    blob = sycon.blob_from_xml(sycon.read_xml(skeleton_paths.sycon_xml))
    _, _, detail = sycon.detail_block(blob)
    return _pn_image_size(detail)


def skeleton_sizes(skeleton_paths) -> tuple[int, int]:
    """(in_bytes, out_bytes) of a SyCon EtherNet/IP skeleton project."""
    m = load(skeleton_paths)
    iu = (max((s.bit_offset + s.bits for s in m.inp.signals), default=0) + 7) // 8
    ou = (max((s.bit_offset + s.bits for s in m.out.signals), default=0) + 7) // 8
    return iu, ou


def skeleton_dtypes(skeleton_paths) -> set:
    """Data types for which the skeleton provides a write template."""
    m = load(skeleton_paths)
    return {s.sycon_dtype for s in m.inp.signals + m.out.signals}
