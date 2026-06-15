"""Read (and later write) the SYCON_net.xml DTM store.

The configuration lives in a <BinData dt:dt="bin.hex"> hex blob:
  [length-prefixed UTF-16 records: <Protocol> schema with params, topology module]
  [embedded OLE2 container - size-independent, never touched]
  [detail record: u32 length @ anchor-4, then the signal table as UTF-16 XML]

Format details: docs/STRUCTURE.md. Write-back reuses prototype/spj_inject.py logic.
"""
from __future__ import annotations
import re
import struct
import base64

from .model import ConfigModel, Interface, Signal, DeviceInfo

ANCHOR = '<Module  systemTag="'.encode("utf-16-le")   # two spaces -> detailed module


def read_xml(path) -> str:
    return open(path, "rb").read().decode("utf-8", "replace")


def blob_from_xml(xmltext: str) -> bytes:
    m = re.search(r"<BinData[^>]*>(.*?)</BinData>", xmltext, re.S)
    hx = re.sub(r"[^0-9A-Fa-f]", "", m.group(1))
    return bytes.fromhex(hx)


def _param(text: str, pid: str):
    m = re.search(rf'id="{pid}"[^>]*default="([^"]*)"', text)
    return m.group(1) if m else None


def _detail_text(blob: bytes) -> str:
    i = blob.find(ANCHOR)
    if i < 0:
        raise ValueError("detail anchor not found")
    return blob[i:].decode("utf-16-le").rstrip("\x00")


# the detail module anchor: usually two spaces; some EtherNet/IP variants
# (byteOrder="big") use a single space. Both are double-quote (vs. the
# single-quote topology module), so this stays unambiguous.
ANCHOR_ALT = '<Module systemTag="'.encode("utf-16-le")


def detail_block(blob: bytes):
    """Framework-level: locate the length-prefixed detail record. Returns
    (anchor, declen, text). The u32 right before the anchor is the byte length
    of the detail incl. the trailing \\0\\0; this bounds the UTF-16 text so it
    works even when binary (OLE2 / CIP assembly) data follows it (EtherNet/IP).
    Tries the two-space and one-space module anchors and validates the length
    field decodes cleanly (so an unsupported variant raises rather than crashes)."""
    for anchor in (ANCHOR, ANCHOR_ALT):
        i = blob.find(anchor)
        if i < 4:
            continue
        declen = struct.unpack_from("<I", blob, i - 4)[0]
        if not (0 < declen <= len(blob) - i):
            continue
        try:
            text = blob[i:i + declen].decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            continue
        if "</Module>" in text:
            return i, declen, text
    raise ValueError("detail anchor not found (unsupported SyCon variant)")


def _parse_signals(detail: str, direction: str) -> list[Signal]:
    addr = "1" if direction == "In" else "2"
    m = re.search(rf'<Module\s+systemTag="[^"]+"\s+displayName="\d+ Bytes (?:In|Out)"'
                  rf'[^>]*moduleAddress="{addr}".*?</Module>', detail, re.S)
    sigs: list[Signal] = []
    if not m:
        return sigs
    offsets: list[int | None] = []
    for sm in re.finditer(r"<Signal\b(.*?)</Signal>", m.group(0), re.S):
        s = sm.group(0)
        sigs.append(Signal(
            name=re.search(r'displayName="([^"]+)"', s).group(1),
            sycon_dtype=re.search(r'dataType="(\w+)"', s).group(1),
            array_elements=int(re.search(r'arrayElements="(\d+)"', s).group(1)),
            systemtag=re.search(r'systemTag="([^"]+)"', s).group(1),
        ))
        ap = re.search(r'signalAccessPath="(\d+)', s)   # byte offset (drops ".0")
        offsets.append(int(ap.group(1)) if ap else None)
    # reconstruct reserved gaps (pad_before) from the explicit byte offsets, so
    # a configuration with gaps round-trips identically. Files without gaps and
    # without parseable offsets stay contiguous (pad_before = 0).
    running = 0
    for sig, off in zip(sigs, offsets):
        if off is not None and off > running:
            sig.pad_before = off - running
        running = (off if off is not None else running + sig.pad_before) + sig.size
    return sigs


def _module_tag(text: str, direction: str) -> str:
    addr = "1" if direction == "In" else "2"
    m = re.search(rf"moduleType='\d+ Bytes (?:In|Out)' moduleAddress='{addr}'", text)
    # topology module systemTag (single-quote record)
    m = re.search(rf"<Module systemTag='([^']+)'[^>]*moduleAddress='{addr}'", text)
    return m.group(1) if m else ""


def load(path) -> ConfigModel:
    """Parse SYCON_net.xml into a ConfigModel (device + In/Out interfaces)."""
    xmltext = read_xml(path)
    blob = blob_from_xml(xmltext)
    text = blob.decode("utf-16-le", "replace")

    prot = re.search(r'<Protocol id="\d+" name="([^"]+)" firmware="([^":]+)', text)
    dev = DeviceInfo(
        protocol=(prot.group(1) if prot else ""),
        firmware=(prot.group(2) if prot else ""),
        node_id=int(_param(text, "NODE_ID") or 0) or None,
        node_name=_param(text, "DNS_NODE_NAME") or "",
        vendor_id=int(_param(text, "VENDOR_ID") or "0", 0) or None,
        product_code=int(_param(text, "PRODUCT_CODE") or "0", 0) or None,
    )
    in_len = int(_param(text, "INPUT_LENGTH") or 0)
    out_len = int(_param(text, "OUTPUT_LENGTH") or 0)
    detail = _detail_text(blob)

    inp = Interface("In", in_len, _module_tag(text, "In"), _parse_signals(detail, "In"))
    out = Interface("Out", out_len, _module_tag(text, "Out"), _parse_signals(detail, "Out"))
    return ConfigModel(dev, inp, out, raw={"sycon_path": str(path)})
