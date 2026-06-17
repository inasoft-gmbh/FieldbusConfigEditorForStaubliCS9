"""EtherNet/IP structural editing (Hilscher NETX 51 RE/EIS adapter). BLOB IS MASTER.

Reverse-engineered (Desktop/_fbce_eip_struct/, 6 reference projects):
- Signals are UTF-16 XML embedded in the CFB stream `CahedAdapter/EISAdapterBasic`
  (note Hilscher typo "Cahed"): a fixed header, a u32 XML byte-length @127, then the XML —
  ONE `<Module  systemTag=.. moduleType="Connect1">` holding BOTH directions' `<Signal>`s
  (signalType="input" -> In, "output" -> Out). Same `<Signal>` format as EtherCAT
  ECTDeviceBasic: displayName/signalType/signalAccessPath/dataType/arrayElements +
  `<Property id="6100">` (per-signal GUID, base64) + `<Property id="6103">`
  (= base64(u32 accessPath*8), the bit offset).
- The fixed interface SIZE (assembly budget) is in
  `CahedAdapter/CIPCoCoMgr/STLConnectMap/0/CIPConnectBasic` u16 @50 (In) / @78 (Out).
- KEY: subdividing/editing signals within the same total size changes ONLY EISAdapterBasic
  (proven: size_64x48 vs mixed_types differ in EISAdapterBasic alone) — NO size/configMD5/
  .nxd change. The .nxd is constant across all configs; configMD5 = f(in_size,out_size) only,
  so it is unchanged by signal edits. Resize changes the CIPConnectBasic sizes (+ configMD5).
- No per-byte streams (EtherCAT had PdoEntry streams; EIP uses the CIP assembly = just the
  byte count above) -> simpler than EtherCAT.
"""
from __future__ import annotations
import io
import struct
import re
import base64
import uuid
import olefile
from . import cfb_write
from .blob_ec import _set_attr, _set_prop, _fresh_6100
from .datatypes import bit_width

_AD = "CahedAdapter"
_EA = "EISAdapterBasic"
_LEN_OFF = 127                                  # u32 XML byte-length in the EISAdapterBasic header
_SIZE_OFF = {"input": 50, "output": 78}         # CIPConnectBasic u16 assembly byte counts
_CONN = ("CIPCoCoMgr", "STLConnectMap", "0", "CIPConnectBasic")


def _split_blob(blob: bytes):
    return blob[:4], blob[4:]


def _tree(cfb: bytes) -> dict:
    return cfb_write.read_tree(olefile.OleFileIO(io.BytesIO(cfb)))


def _ea_xmlstart(ea: bytes) -> int:
    return ea.find(b"<\x00")                     # first '<' (UTF-16) = start of the XML


def _ea_xml(ea: bytes) -> str:
    return ea[_ea_xmlstart(ea):].decode("utf-16-le", "replace")


def _put_ea_xml(ea: bytes, xml: str) -> bytes:
    """Reassemble EISAdapterBasic: keep the header bytes @0..126, write the new XML byte
    length @127 (u32), then the new UTF-16 XML. (Header @0..126 carries no signal count —
    verified across the references — so it is preserved verbatim.)"""
    enc = xml.encode("utf-16-le")
    return bytes(ea[:_LEN_OFF]) + struct.pack("<I", len(enc)) + enc


def _conn(tree):
    cur = tree.get(_AD)
    for k in _CONN:
        cur = cur.get(k) if isinstance(cur, dict) else None
    return cur


def read_signals(blob: bytes):
    """[(direction, displayName, dataType, arrayElements, accessPath, systemTag)] from the
    EISAdapterBasic XML. direction = 'input'/'output' (the signalType)."""
    cfb = blob[4:]
    ea = _tree(cfb).get(_AD, {}).get(_EA)
    if not isinstance(ea, (bytes, bytearray)):
        return []
    xml = _ea_xml(bytes(ea))
    out = []
    for s in re.finditer(r'<Signal\b([^>]*)>', xml):
        a = s.group(1)
        g = lambda k: (re.search(rf'{k}="([^"]*)"', a) or [None, None])[1]
        out.append((g("signalType"), g("displayName"), g("dataType"),
                    g("arrayElements"), g("signalAccessPath"), g("systemTag")))
    return out


def assembly_sizes(blob: bytes):
    """(in_bytes, out_bytes) — the configured assembly sizes from CIPConnectBasic."""
    cc = _conn(_tree(blob[4:]))
    if not isinstance(cc, (bytes, bytearray)):
        return None, None
    cc = bytes(cc)
    return (struct.unpack_from("<H", cc, _SIZE_OFF["input"])[0],
            struct.unpack_from("<H", cc, _SIZE_OFF["output"])[0])


def rebuild_module_signals(xml: str, direction: str, desired) -> str:
    """Rebuild the `direction` ('input'/'output') signals of the single Connect `<Module>` in
    an EIP signal XML (works for both the EISAdapterBasic stream AND the Val3 ProcessData —
    same format). `desired` = ordered (systemtag, name, sycon_dtype, array_elements). Keeps
    the other direction; preserves 6100 by systemTag (fresh otherwise); accessPath/6103
    recomputed contiguously; signals ordered input-then-output. Returns the new XML string."""
    m = re.search(r'(<Module\b[^>]*>)(.*?)(</Module>)', xml, re.S)
    if not m:
        return xml
    body = m.group(2)
    blocks = list(re.finditer(r'<Signal\b.*?</Signal>', body, re.S))
    if not blocks:
        return xml                               # need a template
    lead = body[:blocks[0].start()]              # module <Property>s + whitespace
    sep = (body[blocks[0].end():blocks[1].start()] if len(blocks) > 1 else "\n\t\t")
    trailer = body[blocks[-1].end():]
    template = blocks[0].group(0)

    def styp(b):
        return (re.search(r'signalType="([^"]*)"', b) or [None, ""])[1]

    def tag(b):
        return (re.search(r'systemTag="([^"]*)"', b) or [None, ""])[1]

    old_by_tag = {tag(b.group(0)): b.group(0) for b in blocks}
    keep = [b.group(0) for b in blocks if styp(b.group(0)) != direction]   # other direction

    new_blocks, access = [], 0
    for t, name, dtype, ae in desired:
        ae = int(ae)
        width = bit_width(dtype, ae) // 8       # bytes (all data types, incl. signed/dword)
        blk = old_by_tag.get(t, template)
        blk = _set_attr(blk, "systemTag", t)
        blk = _set_attr(blk, "displayName", name)
        blk = _set_attr(blk, "signalType", direction)
        blk = _set_attr(blk, "dataType", dtype)
        blk = _set_attr(blk, "arrayElements", str(ae))
        blk = _set_attr(blk, "signalAccessPath", str(access))
        if t not in old_by_tag:
            blk = _set_prop(blk, "6100", _fresh_6100())
        blk = _set_prop(blk, "6103",
                        base64.b64encode(struct.pack("<I", access * 8)).decode("ascii"))
        new_blocks.append(blk)
        access += width

    # reassemble: module props + input signals + output signals (stable order)
    ins = (new_blocks if direction == "input" else
           [b for b in keep if styp(b) == "input"])
    outs = ([b for b in keep if styp(b) == "output"] if direction == "input"
            else new_blocks)
    ordered = ins + outs
    new_body = lead + sep.join(ordered) + trailer
    return xml[:m.start(2)] + new_body + xml[m.end(2):]


def set_direction_signals(blob: bytes, direction: str, desired) -> bytes:
    """Rebuild the `direction` signals in the blob's EISAdapterBasic XML (the SyCon master)
    via rebuild_module_signals, update the header XML-length, cfb_write. Only EISAdapterBasic
    changes (size/configMD5/.nxd untouched for signal edits). desired = ordered
    (systemtag, name, sycon_dtype, array_elements)."""
    prefix, cfb = _split_blob(blob)
    tree = _tree(cfb)
    ad = tree.get(_AD)
    if not isinstance(ad, dict) or not isinstance(ad.get(_EA), (bytes, bytearray)):
        return blob
    ea = bytes(ad[_EA])
    new_xml = rebuild_module_signals(_ea_xml(ea), direction, desired)
    ad[_EA] = _put_ea_xml(ea, new_xml)
    new_cfb = cfb_write.build(tree)
    return struct.pack("<I", len(new_cfb)) + new_cfb


def set_assembly_sizes(blob: bytes, in_bytes: int, out_bytes: int) -> bytes:
    """Patch the assembly byte counts in the blob's CIPConnectBasic (@50 In / @78 Out) IN
    PLACE (same-size, keeps the CFB valid). This is SyCon's display side of a resize; the
    robot-critical size + checksum live in the .nxd (see set_nxd_sizes). NOTE: EISConnectBasic
    encodes connection GUIDs and configMD5 = f(sizes) is a SyCon-internal hash we do NOT
    reproduce — so a resized blob should be re-validated in SyCon (B1: nxd-correct for the
    robot; SyCon may re-derive its own hashes on open/save)."""
    prefix, cfb = _split_blob(blob)
    ole = olefile.OleFileIO(io.BytesIO(cfb))
    name = "/".join((_AD,) + _CONN)
    cc = bytearray(ole.openstream(name).read())
    struct.pack_into("<H", cc, _SIZE_OFF["input"], in_bytes)
    struct.pack_into("<H", cc, _SIZE_OFF["output"], out_bytes)
    old = ole.openstream(name).read()
    i = cfb.find(old)
    if i < 0 or cfb.count(old) != 1:
        return blob                              # can't locate uniquely -> leave (nxd carries it)
    patched = cfb[:i] + bytes(cc) + cfb[i + len(cc):]
    return struct.pack("<I", len(patched)) + patched


# EIP .nxd size fields (this NETX 51 RE/EIS variant): u16 @1141 = In*8 bit, @1181 = Out*8;
# integrity MD5 @84..99 = md5(nxd[136:]) (standard netX offset 0x54, as in nxd_ec).
_NXD_IN_BITS, _NXD_OUT_BITS, _NXD_MD5 = 1141, 1181, 84


def set_nxd_sizes(nxd: bytes, in_bytes: int, out_bytes: int) -> bytes:
    """Write the assembly sizes into the exported EIP .nxd (what the robot reads): @1141 =
    In*8 bit, @1181 = Out*8 bit, then recompute the netX integrity MD5 @84..99 = md5(data
    [136:]). Guarded: only applies when @84 already holds md5(nxd[136:]) (confirms the
    layout/variant); otherwise returns the .nxd unchanged."""
    import hashlib
    if len(nxd) < _NXD_OUT_BITS + 2:
        return nxd
    if bytes(nxd[_NXD_MD5:_NXD_MD5 + 16]) != hashlib.md5(bytes(nxd[136:])).digest():
        return nxd                               # not this nxd layout -> don't touch
    b = bytearray(nxd)
    struct.pack_into("<H", b, _NXD_IN_BITS, in_bytes * 8)
    struct.pack_into("<H", b, _NXD_OUT_BITS, out_bytes * 8)
    b[_NXD_MD5:_NXD_MD5 + 16] = hashlib.md5(bytes(b[136:])).digest()
    return bytes(b)
