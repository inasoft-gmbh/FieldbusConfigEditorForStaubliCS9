"""Writers: turn the in-memory ConfigModel back into the three on-disk files.

Strategy (lowest risk): use the EXISTING files as skeletons and clone per-type
signal templates from them (proven byte format), substituting name / UUID /
offset / arrayElements. Sizes/node/IP are patched in place. After writing, the
caller re-parses and verifies (round-trip self-check).

Reuses the cracked formats documented in docs/ (STRUCTURE, 06_NXD, 07_VAL3).
"""
from __future__ import annotations
import re
import struct
import base64
import uuid
import hashlib

from .sycon import read_xml, blob_from_xml, ANCHOR
from .datatypes import by_sycon


# ----------------------------------------------------------------- helpers
def _guid_to_6100(g: str) -> str:
    b = uuid.UUID(g).bytes
    le = struct.pack("<IHH", *struct.unpack(">IHH", b[:8])) + b[8:]
    return base64.b64encode(le).decode()


def _off_b64(bit_off: int) -> str:
    return base64.b64encode(struct.pack("<I", bit_off)).decode()


def _sub(attr, value, text):
    return re.sub(rf'({attr}=")[^"]*(")', lambda m: m.group(1) + str(value) + m.group(2),
                  text, count=1)


# ============================================================ SYCON_net.xml
def _sycon_templates(detail: str, direction: str):
    """Return (wrapper_head, {dtype: core_signal_text}, {uid: core_signal_text}).
    by_uid lets an existing signal re-use its OWN block so per-signal formatting is
    preserved byte-exact (e.g. compact single-bit blocks vs expanded byte-bit
    blocks); dtype templates are the fallback for newly added signals."""
    addr = "1" if direction == "In" else "2"
    m = re.search(rf'<Module\s+systemTag="[^"]+"\s+displayName="\d+ Bytes (?:In|Out)"'
                  rf'[^>]*moduleAddress="{addr}".*?</Module>', detail, re.S)
    if not m:
        raise ValueError(f"SYCON: module {direction} not found in skeleton")
    body = m.group(0)
    sig0 = body.find("<Signal")
    wrapper_head = body[:sig0]               # <Module ...>\r\n\t<Property6100/><Property6102/>
    templates, by_uid = {}, {}
    for sm in re.finditer(r"<Signal\b.*?</Signal>", body, re.S):
        blk = sm.group(0)
        dt = re.search(r'dataType="(\w+)"', blk).group(1)
        uid = re.search(r'systemTag="([^"]+)"', blk).group(1)
        templates.setdefault(dt, blk)
        by_uid[uid] = blk
    return wrapper_head, templates, by_uid


def _render_sycon_signal(template: str, sig, byte_off: int, bit_index: int = 0) -> str:
    dt = by_sycon(sig.sycon_dtype)
    ap = f"{byte_off}.{bit_index}" if dt.key == "bit" else f"{byte_off}"
    t = template
    t = _sub("systemTag", sig.systemtag, t)
    t = _sub("displayName", sig.name, t)
    t = _sub("signalAccessPath", ap, t)
    t = _sub("dataType", sig.sycon_dtype, t)
    t = _sub("arrayElements", sig.array_elements, t)
    # first 6100 (after systemTag) = the signal GUID in MS binary
    t = re.sub(r'(id="6100"[^>]*value=")[^"]*(")',
               lambda m: m.group(1) + _guid_to_6100(sig.systemtag) + m.group(2), t, count=1)
    t = re.sub(r'(id="6103"[^>]*value=")[^"]*(")',
               lambda m: m.group(1) + _off_b64(byte_off * 8 + bit_index) + m.group(2), t, count=1)
    return t


def _iter_offsets(signals):
    """Yield (sig, byte_off, bit_index) for each signal. Single sub-byte bits
    (dataType 'bit', arrayElements not a multiple of 8) advance a bit counter
    within the byte (0..7); the byte-granular model encodes a new byte as
    pad_before on the next signal. Full-byte bits (ae=8) and other types use
    bit_index 0 -> unchanged behaviour."""
    off, bit_in_byte, prev_off = 0, 0, -1
    for sig in signals:
        off += sig.pad_before
        if off != prev_off:
            bit_in_byte = 0
        dt = by_sycon(sig.sycon_dtype)
        sub_bit = dt.key == "bit" and sig.array_elements % 8 != 0
        bidx = bit_in_byte if sub_bit else 0
        yield sig, off, bidx
        if sub_bit:
            bit_in_byte += sig.array_elements
        prev_off = off
        off += sig.size


def _build_sycon_detail(model, skeleton_detail: str) -> str:
    mods = []
    for iface in model.interfaces():
        wrapper, templates, by_uid = _sycon_templates(skeleton_detail, iface.direction)
        wrapper = re.sub(r'\d+( Bytes (?:In|Out)")',
                         lambda m: f"{iface.max_bytes}{m.group(1)}", wrapper)
        cores = []
        for sig, off, bidx in _iter_offsets(iface.signals):
            tmpl = (by_uid.get(sig.systemtag) or templates.get(sig.sycon_dtype)
                    or next(iter(templates.values())))
            cores.append(_render_sycon_signal(tmpl, sig, off, bidx))
        body = wrapper + cores[0] if cores else wrapper
        for c in cores[1:]:
            body += "\r\n\t" + c
        body += "\r\n</Module>"
        mods.append(body)
    return "\n".join(mods) + "\n"


def _patch_record_string(s: str, model) -> str:
    """Apply size/node edits to a single record's string content."""
    if 'id="INPUT_LENGTH"' in s:
        s = re.sub(r'(id="INPUT_LENGTH"[^>]*default=")\d+(")',
                   lambda m: m.group(1) + str(model.inp.max_bytes) + m.group(2), s)
        s = re.sub(r'(id="OUTPUT_LENGTH"[^>]*default=")\d+(")',
                   lambda m: m.group(1) + str(model.out.max_bytes) + m.group(2), s)
        if model.device.node_id is not None:
            s = re.sub(r'(id="NODE_ID"[^>]*default=")\d+(")',
                       lambda m: m.group(1) + str(model.device.node_id) + m.group(2), s)
    # The network (DNS) node name is a blob parameter (datatype string) — the BLOB
    # is the master, so a name change must be written here, not only into the .nxd.
    # _rewrite_records re-emits the record with a corrected length prefix, so a name
    # of any length is safe. (Lives in the same param record as INPUT_LENGTH, but we
    # match independently in case a variant splits them.)
    if 'id="DNS_NODE_NAME"' in s and model.device.node_name is not None:
        s = re.sub(r'(id="DNS_NODE_NAME"[^>]*default=")[^"]*(")',
                   lambda m: m.group(1) + model.device.node_name + m.group(2), s)
    if "Bytes In'" in s or "InBytes'" in s:
        s = re.sub(r"\d+( Bytes In')", lambda m: f"{model.inp.max_bytes}{m.group(1)}", s)
        s = re.sub(r"\d+( InBytes')", lambda m: f"{model.inp.max_bytes}{m.group(1)}", s)
        s = re.sub(r"(InBytes'[^>]*arrayElements=')\d+(')",
                   lambda m: m.group(1) + str(model.inp.max_bytes) + m.group(2), s)
    if "Bytes Out'" in s or "OutBytes'" in s:
        s = re.sub(r"\d+( Bytes Out')", lambda m: f"{model.out.max_bytes}{m.group(1)}", s)
        s = re.sub(r"\d+( OutBytes')", lambda m: f"{model.out.max_bytes}{m.group(1)}", s)
        s = re.sub(r"(OutBytes'[^>]*arrayElements=')\d+(')",
                   lambda m: m.group(1) + str(model.out.max_bytes) + m.group(2), s)
    return s


def _rewrite_records(blob: bytes, ole2_start: int, model) -> bytes:
    """Parse length-prefixed UTF-16 records, patch sizes/node, re-emit with
    correct length prefixes (handles changes in digit count)."""
    out = bytearray()
    i = 0
    while i < ole2_start:
        if i + 4 > ole2_start:
            out += blob[i:ole2_start]
            break
        ln = struct.unpack_from("<I", blob, i)[0]
        if 4 <= ln and i + 4 + ln <= ole2_start and blob[i + 4 + ln - 2:i + 4 + ln] == b"\x00\x00":
            s = blob[i + 4:i + 4 + ln - 2].decode("utf-16-le")
            ns = _patch_record_string(s, model)
            if ns == s:
                out += blob[i:i + 4 + ln]                  # unchanged -> keep bytes
            else:
                enc = ns.encode("utf-16-le")
                out += struct.pack("<I", len(enc) + 2) + enc + b"\x00\x00"
            i += 4 + ln
        else:
            out += blob[i:i + 1]                            # non-record byte (padding)
            i += 1
    return bytes(out)


def write_sycon(model, original_xml_path) -> bytes:
    xmltext = read_xml(original_xml_path)
    blob = blob_from_xml(xmltext)
    ole2_start = blob.find(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    detail_start = blob.find(ANCHOR)

    rec_bytes = _rewrite_records(blob, ole2_start, model)

    skeleton_detail = blob[detail_start:].decode("utf-16-le").rstrip("\x00")
    new_detail = _build_sycon_detail(model, skeleton_detail)
    detail_bytes = (new_detail + "\x00").encode("utf-16-le")

    head = bytearray(rec_bytes) + bytearray(blob[ole2_start:detail_start])
    struct.pack_into("<I", head, len(head) - 4, len(detail_bytes))   # detail length field
    new_blob = bytes(head) + detail_bytes

    hexlo = new_blob.hex()
    return re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                  lambda m: m.group(1) + hexlo + m.group(2), xmltext, flags=re.S
                  ).encode("utf-8")


# ============================================================ J207J208.xml
def _val3_templates(text: str, direction: str):
    addr = "1" if direction == "In" else "2"
    m = re.search(rf'(<Module\b[^>]*moduleAddress="{addr}">\s*<Property\b[^>]*/>)'
                  rf'(.*?)(\r?\n\t\t</Module>)', text, re.S)
    if not m:
        raise ValueError(f"VAL3: module {direction} not found")
    head, sigs, tail = m.group(1), m.group(2), m.group(3)
    templates = {}
    # capture the FULL leading whitespace (incl. a CR of a CRLF line ending), so
    # CRLF val3 files round-trip byte-exact, not just LF ones.
    for sm in re.finditer(r"(\s*)<Signal\b.*?</Signal>", sigs, re.S):
        dt = re.search(r'dataType="(\w+)"', sm.group(0)).group(1)
        templates.setdefault(dt, sm.group(0))     # incl. leading whitespace
    return head, templates, tail


def _render_val3_signal(template: str, sig, byte_off: int, bit_index: int = 0) -> str:
    dt = by_sycon(sig.sycon_dtype)
    ap = f"{byte_off}.{bit_index}" if dt.key == "bit" else f"{byte_off}"
    t = _sub("systemTag", sig.systemtag, template)
    t = _sub("displayName", sig.name, t)
    t = _sub("signalAccessPath", ap, t)
    t = _sub("dataType", sig.sycon_dtype, t)
    t = _sub("arrayElements", sig.array_elements, t)
    t = re.sub(r'(id="6103"[^>]*value=")[^"]*(")',
               lambda m: m.group(1) + _off_b64(byte_off * 8 + bit_index) + m.group(2), t, count=1)
    return t


def write_val3(model, original_xml_path) -> bytes:
    text = read_xml(original_xml_path)
    if model.device.node_id is not None:
        text = re.sub(r'(stationAddress=")Addr \d+(")',
                      lambda m: f"{m.group(1)}Addr {model.device.node_id}{m.group(2)}", text)
    for iface in model.interfaces():
        addr = "1" if iface.direction == "In" else "2"
        head, templates, tail = _val3_templates(text, iface.direction)
        head = re.sub(r'\d+( Bytes (?:In|Out))', lambda m: f"{iface.max_bytes}{m.group(1)}", head)
        parts = []
        for sig, off, bidx in _iter_offsets(iface.signals):
            tmpl = templates.get(sig.sycon_dtype) or next(iter(templates.values()))
            parts.append(_render_val3_signal(tmpl, sig, off, bidx))
        block = head + "".join(parts) + tail
        text = re.sub(rf'<Module\b[^>]*moduleAddress="{addr}">\s*<Property\b[^>]*/>.*?\n\t\t</Module>',
                      lambda m: block, text, count=1, flags=re.S)
    return text.encode("utf-8")


# ================================================================ .nxd
def write_nxd(model, original_nxd_path) -> bytes:
    d = bytearray(open(original_nxd_path, "rb").read())
    struct.pack_into("<H", d, 324, model.inp.max_bytes)
    struct.pack_into("<H", d, 326, model.out.max_bytes)
    if model.device.node_id is not None:
        d[364] = model.device.node_id & 0xFF
    if model.device.ip:
        a, b, c, e = (int(x) for x in model.device.ip.split("."))
        d[360], d[361], d[362], d[363] = e, c, b, a
    if model.device.node_name is not None:
        name = model.device.node_name.encode("latin1")[:15]
        d[328:328 + 16] = name + b"\x00" * (16 - len(name))
    d[0x54:0x54 + 16] = hashlib.md5(bytes(d[136:])).digest()   # recompute checksum
    return bytes(d)
