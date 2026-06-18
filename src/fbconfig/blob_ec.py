"""EtherCAT structural blob editing (Task #13) — delete/add/edit signals in the SyCon
project blob, analog to blob_pn for PROFINET. BLOB IS MASTER.

Layout (reverse-engineered, see Desktop/_fbce_ec_struct/RE_findings.md):
- EtherCAT signals are a PROFINET-format device XML embedded in CFB stream
  `CachedSlave/ECTDeviceBasic`: u32 length @282, utf-16 XML @286 (the XML is the tail).
  Modules: moduleType="RxPdo" (signalType input) / "TxPdo" (output); each <Signal> has
  systemTag/displayName/signalType/signalAccessPath/dataType/arrayElements.
- Each ACTIVE signal also has a PdoEntry CFB stream + name:
  `CachedSlave/ProcessDataMgr/PdoMgr/{Rx,Tx}PdoMap/0/PdoEntryList/<m>/ECTPdoEntryBasic`
  (+ `EntryNameMap/NameMap`). RxPdoMap/0 = active inputs, TxPdoMap/0 = active outputs
  (RxPdoMap/1..5 = the catalog, untouched).
- SM data length: `ProcessDataMgr/SmMgr/SmMap/2/ECTSyncManagerBasic` @21 (In/RxPdo) and
  `/3/...` @21 (Out/TxPdo) = byte count per direction.

The .nxd recompile (netX DBM, like PROFINET main.nxd) is handled separately (nxd_dbm).
"""
from __future__ import annotations
import io
import struct
import re
import olefile
from . import cfb_write

_DEV = "CachedSlave/ECTDeviceBasic"
_LEN_OFF = 282
_XML_OFF = 286
_SM = {"input": 2, "output": 3}          # SmMap index per direction
_PDOMAP = {"input": "RxPdoMap", "output": "TxPdoMap"}


def _split_blob(blob: bytes):
    """(u32-prefix bytes, cfb bytes). blob = <u32 cfb-len><cfb>."""
    return blob[:4], blob[4:]


def _tree(cfb: bytes) -> dict:
    return cfb_write.read_tree(olefile.OleFileIO(io.BytesIO(cfb)))


def _dev_xml(dev: bytes):
    ln = struct.unpack_from("<I", dev, _LEN_OFF)[0]
    return dev[_XML_OFF:_XML_OFF + ln].decode("utf-16-le", "replace"), ln


def _put_dev_xml(dev: bytes, xml: str) -> bytes:
    enc = xml.encode("utf-16-le")
    return bytes(dev[:_LEN_OFF]) + struct.pack("<I", len(enc)) + enc


def read_signals(blob: bytes):
    """[(direction, displayName, dataType, arrayElements, accessPath)] from the blob XML."""
    _, cfb = _split_blob(blob)
    dev = _tree(cfb).get("CachedSlave", {}).get("ECTDeviceBasic")
    if not isinstance(dev, (bytes, bytearray)):
        return []
    xml, _ = _dev_xml(bytes(dev))
    out = []
    for mod in re.finditer(r'<Module\b[^>]*moduleType="(RxPdo|TxPdo)"[^>]*>(.*?)</Module>',
                           xml, re.S):
        direction = "input" if mod.group(1) == "RxPdo" else "output"
        for s in re.finditer(r'<Signal\b([^>]*)>', mod.group(2)):
            a = s.group(1)
            g = lambda k: (re.search(rf'{k}="([^"]*)"', a) or [None, None])[1]
            out.append((direction, g("displayName"), g("dataType"),
                        g("arrayElements"), g("signalAccessPath")))
    return out


_XML_WIDTH = {"bit": None, "unsigned8": 1, "byte": 1, "word": 2, "real32": 4, "real": 4}


def _sig_width_bytes(sig_attrs: str) -> int:
    """Byte width of a <Signal ...> from its dataType + arrayElements (bit: ae/8)."""
    dt = (re.search(r'dataType="([^"]*)"', sig_attrs) or [None, ""])[1]
    ae = int((re.search(r'arrayElements="(\d+)"', sig_attrs) or [None, "1"])[1])
    w = _XML_WIDTH.get(dt)
    return ae // 8 if w is None else w * ae


def _remove_last_signal_xml(xml: str, direction: str):
    """Remove the LAST <Signal>...</Signal> of the RxPdo/TxPdo module. Returns
    (xml, width_bytes) or (xml, None) if none."""
    mtype = "RxPdo" if direction == "input" else "TxPdo"
    m = re.search(rf'(<Module\b[^>]*moduleType="{mtype}"[^>]*>)(.*?)(</Module>)', xml, re.S)
    if not m:
        return xml, None
    body = m.group(2)
    sigs = list(re.finditer(r'\s*<Signal\b(.*?)</Signal>', body, re.S))
    if not sigs:
        return xml, None
    last = sigs[-1]
    width = _sig_width_bytes(last.group(1))
    new_body = body[:last.start()] + body[last.end():]
    new_xml = xml[:m.start(2)] + new_body + xml[m.end(2):]
    return new_xml, width


def _guid_bytes(systemtag: str) -> bytes:
    """SyCon systemTag string -> the 16 raw bytes as stored in the .nxd 0x17 row (string
    order, e.g. 'A73080D9-E3A3-...'-> a7 30 80 d9 ...)."""
    return bytes.fromhex(systemtag.replace("-", ""))


def delete_signals_nxd(nxd_bytes: bytes, systemtags, in_bytes_removed: int,
                       out_bytes_removed: int) -> bytes:
    """Delete signal rows (by systemTag, 16-byte string-order GUIDs) from the EtherCAT
    .nxd: drop the 0x17 row in record[4], rebuild its 0x1f group-dir tail (count + row
    pointers) via nxd_dbm, renumber the group's rows, serialize (recomputes positions/
    pointers/file-size/MD5), then patch the process-image size @380(In)/@408(Out) u16 and
    the MD5. BYTE-EXACT vs SyCon (validated state_2x2->state_1x1). systemtags = iterable
    of 16-byte GUIDs."""
    from . import nxd_dbm
    import hashlib
    tags = {bytes(t) for t in systemtags}
    nxd = nxd_dbm.parse(nxd_bytes)
    rec = nxd.records[4]
    grp = {}
    cur = None
    for it in rec:
        if it.type == 0x1f:
            cur = it
        elif it.type == 0x17:
            grp[id(it)] = cur
    rm = [it for it in rec if it.type == 0x17 and len(it.content) >= 32
          and bytes(it.content[16:32]) in tags]
    if not rm:
        return nxd_bytes
    affected = set()
    for it in rm:
        affected.add(id(grp[id(it)]))
        rec.remove(it)
    for d in [it for it in rec if it.type == 0x1f]:
        if id(d) not in affected:
            continue
        rows, c = [], None
        for it in rec:
            if it.type == 0x1f:
                c = it
            elif it.type == 0x17 and c is d:
                rows.append(it)
        nxd_dbm._rebuild_tail(nxd, d, rows, 0x17)
        for i, it in enumerate(rows):
            struct.pack_into("<I", it.content, 4, i + 1)
    out = bytearray(nxd_dbm.serialize(nxd))
    if in_bytes_removed:
        struct.pack_into("<H", out, 380,
                         struct.unpack_from("<H", out, 380)[0] - in_bytes_removed)
    if out_bytes_removed:
        struct.pack_into("<H", out, 408,
                         struct.unpack_from("<H", out, 408)[0] - out_bytes_removed)
    out[0x54:0x54 + 16] = hashlib.md5(bytes(out[136:])).digest()
    return bytes(out)


_DIR_IDX = {"input": 0, "output": 1}     # group order in .nxd record[4]: In, Out, Diag

# .nxd 0x17 row data-TYPE code @48  (SyCon-internal; RE'd from d_edit/d_big5/d_reorder)
EC_TYPE_CODE = {"bit": 0x80, "unsigned8": 0x84, "word": 0x85, "real32": 0x96}
EC_TYPE_WIDTH = {"bit": 1, "unsigned8": 1, "word": 2, "real32": 4}
_CODE_WIDTH = {0x80: 1, 0x84: 1, 0x85: 2, 0x96: 4}


def _recompute_sizes(out: bytearray, rec) -> None:
    """Set the process-image size @380(In)/@408(Out) u16 = sum of the group rows' widths
    (derived from each 0x17 row's type code @48)."""
    sizes = {"input": 0, "output": 0}
    cur = None
    order = ["input", "output", "diag"]
    gi = -1
    for it in rec:
        if it.type == 0x1f:
            gi += 1
            cur = order[gi] if gi < len(order) else "diag"
        elif it.type == 0x17 and cur in ("input", "output") and len(it.content) > 48:
            sizes[cur] += _CODE_WIDTH.get(it.content[48], 0)
    struct.pack_into("<H", out, 380, sizes["input"])
    struct.pack_into("<H", out, 408, sizes["output"])


def edit_signal_nxd(nxd_bytes: bytes, systemtag, new_type: str) -> bytes:
    """Change a signal's data TYPE in the .nxd (by systemTag): patch its 0x17 row type
    code @48, recompute process-image sizes @380/@408 (the width may change), serialize,
    MD5. PRESERVES the systemTag + name (SyCon regenerates the GUID on type change; we do
    NOT — the PLC links by UUID, see systemtag-must-travel). Validated GUID-masked vs
    d_edit (bit->word)."""
    from . import nxd_dbm
    import hashlib
    tag = bytes(systemtag)
    code = EC_TYPE_CODE.get(new_type)
    if code is None:
        raise ValueError(f"unknown EtherCAT data type {new_type!r}")
    nxd = nxd_dbm.parse(nxd_bytes)
    rec = nxd.records[4]
    row = next((it for it in rec if it.type == 0x17 and len(it.content) >= 32
                and bytes(it.content[16:32]) == tag), None)
    if row is None:
        return nxd_bytes
    row.content[48] = code
    out = bytearray(nxd_dbm.serialize(nxd))
    _recompute_sizes(out, rec)
    out[0x54:0x54 + 16] = hashlib.md5(bytes(out[136:])).digest()
    return bytes(out)


def set_direction_signals_nxd(nxd_bytes: bytes, direction: str, desired) -> bytes:
    """Rebuild ALL rows of `direction` in the .nxd record[4] to match `desired` (ordered
    list of (systemtag, name, sycon_dtype, array_elements)): reuse the existing 0x17 row
    when its systemTag matches (patch index@4/type@48/name), clone the group template for
    new tags, drop rows whose tag is gone; rebuild the 0x1f dir (count + row pointers),
    serialize, recompute sizes + MD5. systemTag travels with the signal. Mirrors the blob
    set_direction_signals. systemtag accepted as 16 bytes or a GUID string."""
    from . import nxd_dbm
    import hashlib
    def tag16(t):
        return bytes(t) if isinstance(t, (bytes, bytearray)) else _guid_bytes(t)
    nxd = nxd_dbm.parse(nxd_bytes)
    rec = nxd.records[4]
    dirs = [it for it in rec if it.type == 0x1f]
    d = dirs[_DIR_IDX[direction]]
    gi = rec.index(d)
    cur = []
    for j in range(gi + 1, len(rec)):
        if rec[j].type == 0x17:
            cur.append(rec[j])
        elif rec[j].type == 0x1f:
            break
    if not cur:
        return nxd_bytes                              # need a template row
    by_tag = {bytes(it.content[16:32]): it for it in cur}
    template = cur[0]
    new_rows = []
    for idx, (tag, name, dtype, ae) in enumerate(desired, 1):
        t16 = tag16(tag)
        row = by_tag.get(t16)
        if row is None:                              # new signal: clone the template
            row = nxd_dbm.Item(0x17, bytearray(template.content))
            row.content[16:32] = t16
        struct.pack_into("<I", row.content, 4, idx)  # group index (1-based)
        code = EC_TYPE_CODE.get(dtype)
        if code is not None and len(row.content) > 48:
            row.content[48] = code
        # name (tail = u32 len + utf-8), rebuilt from the row's existing name slot
        old = bytes(row.content)
        p = len(old)
        for q in range(len(old) - 1, 3, -1):
            run = old[q:]
            if run and all(32 <= b < 127 for b in run) and \
               struct.unpack_from("<I", old, q - 4)[0] == len(run):
                p = q - 4
                break
        enc = name.encode("utf-8")
        row.content = bytearray(old[:p]) + struct.pack("<I", len(enc)) + enc
        struct.pack_into("<I", row.content, 4, idx)
        row.content[16:32] = t16
        if code is not None and len(row.content) > 48:
            row.content[48] = code
        new_rows.append(row)
    # splice the group's rows: drop old, insert new after the dir
    for it in cur:
        rec.remove(it)
    for off, row in enumerate(new_rows):
        rec.insert(gi + 1 + off, row)
    # rebuild the 0x1f dir: 12-byte header (count@4 = rows+1) + one row pointer each
    base = bytearray(d.content[:12])
    struct.pack_into("<I", base, 4, len(new_rows) + 1)
    nxd.pointers = [(s, o, t) for (s, o, t) in nxd.pointers
                    if s is not d and t not in cur]
    for row in new_rows:
        o = len(base)
        base += b"\x00\x00\x00\x00"
        nxd.pointers.append((d, o, row))
    d.content = base
    out = bytearray(nxd_dbm.serialize(nxd))
    _recompute_sizes(out, rec)
    out[0x54:0x54 + 16] = hashlib.md5(bytes(out[136:])).digest()
    return bytes(out)


def reorder_signals_nxd(nxd_bytes: bytes, direction: str, order_tags) -> bytes:
    """Reorder a direction's signal rows in the .nxd record[4] to match `order_tags` (list
    of 16-byte systemTags, the new order), renumber the group index @4, rebuild the 0x1f
    dir's row-pointer slots in the new order, serialize, MD5. PRESERVES systemTag + name
    (SyCon regenerates GUIDs on reorder; we do NOT). Sizes unchanged. Validated GUID-masked
    vs d_reorder."""
    from . import nxd_dbm
    import hashlib
    order = [bytes(t) for t in order_tags]
    nxd = nxd_dbm.parse(nxd_bytes)
    rec = nxd.records[4]
    dirs = [it for it in rec if it.type == 0x1f]
    d = dirs[_DIR_IDX[direction]]
    gi = rec.index(d)
    span = []                                    # (rec_index, row) of this group's rows
    for j in range(gi + 1, len(rec)):
        if rec[j].type == 0x17:
            span.append((j, rec[j]))
        elif rec[j].type == 0x1f:
            break
    by_tag = {bytes(r.content[16:32]): r for _, r in span}
    new_rows = [by_tag[t] for t in order if t in by_tag]
    if len(new_rows) != len(span):
        return nxd_bytes                          # order list incomplete -> no-op
    positions = [j for j, _ in span]
    for pos, row in zip(positions, new_rows):     # write rows back in new order
        rec[pos] = row
        struct.pack_into("<I", row.content, 4, positions.index(pos) + 1)
    # rebuild the dir's row-pointer slots in the new order (keep count @4)
    base = bytearray(d.content[:12])
    nxd.pointers = [(s, o, t) for (s, o, t) in nxd.pointers if s is not d]
    for row in new_rows:
        o = len(base)
        base += b"\x00\x00\x00\x00"
        nxd.pointers.append((d, o, row))
    d.content = base
    out = bytearray(nxd_dbm.serialize(nxd))
    out[0x54:0x54 + 16] = hashlib.md5(bytes(out[136:])).digest()
    return bytes(out)


def add_signal_nxd(nxd_bytes: bytes, direction: str, systemtag, name: str,
                   width_bytes: int, type_code: int | None = None) -> bytes:
    """Add ONE signal row to the EtherCAT .nxd record[4] (inverse of delete): clone a
    same-direction 0x17 row, patch index(@4)/systemTag(@16:32)/name(tail)/bitlen, insert
    after the group's last row, bump its 0x1f dir (count@4 +1, +4-byte row pointer),
    serialize, patch process-image @380(In)/@408(Out) += width_bytes + MD5. The new row
    clones an existing same-direction row (data-type bytes), so the group must be non-empty.
    Round-trips byte-exact with delete (validated state_1x1<->state_2x2). systemtag = 16
    raw bytes."""
    from . import nxd_dbm
    import hashlib
    tag = bytes(systemtag)
    nxd = nxd_dbm.parse(nxd_bytes)
    rec = nxd.records[4]
    dirs = [it for it in rec if it.type == 0x1f]
    d = dirs[_DIR_IDX[direction]]
    # rows of this group + the rec index right after the last one
    gi = rec.index(d)
    rows, end = [], gi + 1
    for j in range(gi + 1, len(rec)):
        if rec[j].type == 0x17:
            rows.append(rec[j])
            end = j + 1
        elif rec[j].type == 0x1f:
            break
    if not rows:
        raise ValueError("EtherCAT add: empty group has no template row to clone")
    old = bytes(rows[-1].content)
    # locate the name = trailing utf-8 run preceded by its u32 length prefix
    p = len(old)
    for q in range(len(old) - 1, 3, -1):
        run = old[q:]
        if run and all(32 <= b < 127 for b in run) and \
           struct.unpack_from("<I", old, q - 4)[0] == len(run):
            p = q - 4
            break
    enc = name.encode("utf-8")
    tmpl = bytearray(old[:p]) + struct.pack("<I", len(enc)) + enc
    struct.pack_into("<I", tmpl, 4, len(rows) + 1)           # group index (1-based)
    tmpl[16:32] = tag                                        # systemTag
    if type_code is not None and len(tmpl) > 48:
        tmpl[48] = type_code & 0xFF                          # data-type code (EC_TYPE_CODE)
    new_row = nxd_dbm.Item(0x17, tmpl)
    rec.insert(end, new_row)
    struct.pack_into("<I", d.content, 4, struct.unpack_from("<I", d.content, 4)[0] + 1)
    off = len(d.content)
    d.content += b"\x00\x00\x00\x00"
    nxd.pointers.append((d, off, new_row))
    out = bytearray(nxd_dbm.serialize(nxd))
    _recompute_sizes(out, rec)
    out[0x54:0x54 + 16] = hashlib.md5(bytes(out[136:])).digest()
    return bytes(out)


def delete_last_signal(blob: bytes, direction: str) -> bytes:
    """Remove the last signal of `direction` ('input'/'output') from the blob: drop its
    <Signal> from the ECTDeviceBasic XML (+ @282 len), remove the highest PdoEntryList
    stream of the active PdoMap, and decrement the SM data length. Returns the new blob
    (same u32-prefix recomputed). No-op if nothing to remove."""
    prefix, cfb = _split_blob(blob)
    tree = _tree(cfb)
    cs = tree.get("CachedSlave")
    if not isinstance(cs, dict) or not isinstance(cs.get("ECTDeviceBasic"), (bytes, bytearray)):
        return blob
    # 1) ECTDeviceBasic XML
    dev = bytes(cs["ECTDeviceBasic"])
    xml, _ = _dev_xml(dev)
    new_xml, width = _remove_last_signal_xml(xml, direction)
    if width is None:
        return blob
    cs["ECTDeviceBasic"] = _put_dev_xml(dev, new_xml)
    # 2) remove the `width` highest PdoEntryList streams (one per byte) of the active PdoMap
    pdomap = cs.get("ProcessDataMgr", {}).get("PdoMgr", {}).get(_PDOMAP[direction], {})
    active = pdomap.get("0") if isinstance(pdomap, dict) else None
    el = active.get("PdoEntryList") if isinstance(active, dict) else None
    if isinstance(el, dict) and el:
        for _ in range(max(1, width)):
            if not el:
                break
            del el[max(el, key=lambda k: int(k))]
    # 3) SM data length -= removed bytes
    sm = cs.get("ProcessDataMgr", {}).get("SmMgr", {}).get("SmMap", {}).get(str(_SM[direction]), {})
    smb = sm.get("ECTSyncManagerBasic") if isinstance(sm, dict) else None
    if isinstance(smb, (bytes, bytearray)) and len(smb) > 21:
        smb = bytearray(smb)
        smb[21] = max(0, smb[21] - width)
        sm["ECTSyncManagerBasic"] = bytes(smb)
    new_cfb = cfb_write.build(tree)
    return struct.pack("<I", len(new_cfb)) + new_cfb


def set_direction_signals(blob: bytes, direction: str, desired, size_bytes=None) -> bytes:
    """Rebuild ALL signals of `direction` in the blob to exactly match `desired` (ordered
    list of (systemtag, name, sycon_dtype, array_elements)). Handles delete/add/edit/
    reorder/resize in one pass: rebuilds the ECTDeviceBasic <Signal> list (preserving each
    signal's 6100 when its systemTag already existed; signalAccessPath/6103 recomputed) and
    the active PdoEntryList to exactly `total_bytes` BYTE entries (reusing existing per-byte
    GUIDs, minting fresh ones for new bytes), and sets the SM data length. `size_bytes` sets
    the configured interface size (SM length) — defaults to the used bytes, but pass the
    interface budget to keep trailing FREE bytes (size > signals). systemTag travels with the
    signal (desired carries it). Returns the new blob (cfb_write rebuild)."""
    import base64, struct as _s, uuid
    prefix, cfb = _split_blob(blob)
    tree = _tree(cfb)
    cs = tree.get("CachedSlave")
    if not isinstance(cs, dict) or not isinstance(cs.get("ECTDeviceBasic"), (bytes, bytearray)):
        return blob
    dev = bytes(cs["ECTDeviceBasic"])
    xml, _ = _dev_xml(dev)
    mtype = "RxPdo" if direction == "input" else "TxPdo"
    m = re.search(rf'(<Module\b[^>]*moduleType="{mtype}"[^>]*>)(.*?)(</Module>)', xml, re.S)
    if not m:
        return blob
    body = m.group(2)
    blocks = list(re.finditer(r'<Signal\b.*?</Signal>', body, re.S))
    if not blocks:
        return blob                                   # need a <Signal> template
    lead = body[:blocks[0].start()]
    trailer = body[blocks[-1].end():]
    sep = (body[blocks[0].end():blocks[1].start()] if len(blocks) > 1 else lead)
    template = blocks[0].group(0)
    old = {(re.search(r'systemTag="([^"]*)"', b.group(0)) or [None, ""])[1]: b.group(0)
           for b in blocks}
    # BIT cursor — replicates Interface.repack_bits exactly: bits pack tight, value
    # (non-bit) types are byte-aligned. This makes single sub-byte bits (arrayElements=1)
    # land at their real bit offset (6103) instead of all stacking at byte 0; byte-aligned
    # signals are unchanged (byte = cur//8, 6103 = cur = byte*8, as before).
    new_blocks, cur = [], 0
    for tag, name, dtype, ae in desired:
        w = _XML_WIDTH.get(dtype)
        if w is not None and cur % 8:                 # byte-align value types
            cur += 8 - (cur % 8)
        width_bits = ae if w is None else w * 8 * ae
        byte, bit = cur // 8, cur % 8
        ap = str(byte) if bit == 0 else f"{byte}.{bit}"   # byte, or byte.bit for a sub-byte bit
        blk = old.get(tag, template)
        blk = _set_attr(blk, "systemTag", tag)
        blk = _set_attr(blk, "displayName", name)
        blk = _set_attr(blk, "dataType", dtype)
        blk = _set_attr(blk, "arrayElements", str(ae))
        blk = _set_attr(blk, "signalAccessPath", ap)
        if tag not in old:
            blk = _set_prop(blk, "6100", _fresh_6100())
        blk = _set_prop(blk, "6103",
                        base64.b64encode(_s.pack("<I", cur)).decode("ascii"))
        new_blocks.append(blk)
        cur += width_bits
    access = (cur + 7) // 8                            # process-image byte span
    new_body = lead + sep.join(new_blocks) + trailer
    cs["ECTDeviceBasic"] = _put_dev_xml(dev, xml[:m.start(2)] + new_body + xml[m.end(2):])
    # PdoEntryList: exactly `access` BYTE entries (reuse existing, clone for the extras)
    el = (cs.get("ProcessDataMgr", {}).get("PdoMgr", {}).get(_PDOMAP[direction], {})
          .get("0", {}).get("PdoEntryList"))
    if isinstance(el, dict) and el:
        existing = [el[k] for k in sorted(el, key=lambda k: int(k))]
        tmpl = existing[-1]                            # a full "BYTE" entry (key5 set)
        rebuilt = []
        for i in range(access):
            if i < len(existing):
                e = existing[i]
            else:
                e = {k: (bytes(v) if isinstance(v, (bytes, bytearray)) else
                         {kk: bytes(vv) for kk, vv in v.items()}) for k, v in tmpl.items()}
                eb = bytearray(_pbag_set(bytes(e["ECTPdoEntryBasic"]), 6,
                                         str(uuid.uuid4()).upper()))
                e["ECTPdoEntryBasic"] = _pbag_set(bytes(eb), 7, _fresh_6100())
            eb = bytearray(e["ECTPdoEntryBasic"])
            _s.pack_into("<I", eb, 16, i + 1)         # byte index (1-based)
            e["ECTPdoEntryBasic"] = bytes(eb)
            rebuilt.append(e)
        el.clear()
        for i, e in enumerate(rebuilt):
            el[str(i)] = e
    # SM data length = configured interface size (>= used bytes -> trailing free allowed)
    sm_len = access if size_bytes is None else max(access, size_bytes)
    sm = cs.get("ProcessDataMgr", {}).get("SmMgr", {}).get("SmMap", {}).get(str(_SM[direction]), {})
    smb = sm.get("ECTSyncManagerBasic") if isinstance(sm, dict) else None
    if isinstance(smb, (bytes, bytearray)) and len(smb) > 21:
        smb = bytearray(smb)
        smb[21] = min(255, sm_len)
        sm["ECTSyncManagerBasic"] = bytes(smb)
    new_cfb = cfb_write.build(tree)
    return struct.pack("<I", len(new_cfb)) + new_cfb


def _set_attr(tag_text: str, key: str, val: str) -> str:
    return re.sub(rf'({key}=")[^"]*(")', lambda m: m.group(1) + val + m.group(2), tag_text)


def _set_prop(block: str, pid: str, value: str) -> str:
    """Set the value="" of a <Property id="pid" .../> inside a <Signal> block."""
    return re.sub(rf'(<Property id="{pid}"[^>]*value=")[^"]*(")',
                  lambda m: m.group(1) + value + m.group(2), block)


def _pbag_set(eb: bytes, key: int, text: str) -> bytes:
    """Replace the utf-16 string of property-bag record `key` ([u32 key][u32 bytelen]
    [utf16-printable]) in an ECTPdoEntryBasic stream (same scan as the decoder: skip the
    binary head, walk record-by-record). Same-length replacement keeps the stream size."""
    i = 0
    while i + 8 <= len(eb):
        k = struct.unpack_from("<I", eb, i)[0]
        ln = struct.unpack_from("<I", eb, i + 4)[0]
        if 0 < ln <= len(eb) - i - 8 and ln % 2 == 0:
            s = eb[i + 8:i + 8 + ln]
            try:
                t = s.decode("utf-16-le")
            except UnicodeDecodeError:
                t = None
            if t is not None and all(31 < ord(c) < 127 or c == "\x00" for c in t):
                if k == key:
                    new = text.encode("utf-16-le") + b"\x00\x00"
                    return bytes(eb[:i + 4] + struct.pack("<I", len(new)) + new + eb[i + 8 + ln:])
                i += 8 + ln
                continue
        i += 2
    return bytes(eb)


def _fresh_6100() -> str:
    import base64, uuid
    return base64.b64encode(uuid.uuid4().bytes).decode("ascii")


def add_signal(blob: bytes, direction: str, systemtag: str, name: str, dtype: str,
               array_elements: int = 1) -> bytes:
    """Append ONE signal to `direction` in the blob (inverse of delete_last_signal): clone
    the last <Signal> of the RxPdo/TxPdo module (patch systemTag/displayName/dataType/
    arrayElements/signalAccessPath), clone `width` PdoEntryList streams (one per byte; patch
    the byte index + NameMap), bump the SM data length. Round-trips byte-exact with delete."""
    w = _XML_WIDTH.get(dtype)
    width = array_elements // 8 if w is None else w * array_elements
    prefix, cfb = _split_blob(blob)
    tree = _tree(cfb)
    cs = tree.get("CachedSlave")
    if not isinstance(cs, dict) or not isinstance(cs.get("ECTDeviceBasic"), (bytes, bytearray)):
        return blob
    dev = bytes(cs["ECTDeviceBasic"])
    xml, _ = _dev_xml(dev)
    mtype = "RxPdo" if direction == "input" else "TxPdo"
    m = re.search(rf'(<Module\b[^>]*moduleType="{mtype}"[^>]*>)(.*?)(</Module>)', xml, re.S)
    if not m:
        return blob
    body = m.group(2)
    sigs = list(re.finditer(r'(\s*)<Signal\b(.*?)</Signal>', body, re.S))
    if not sigs:
        return blob
    import base64, struct as _s, uuid
    last = sigs[-1]
    access = sum(_sig_width_bytes(s.group(2)) for s in sigs)     # next free byte offset
    block = last.group(0)
    block = _set_attr(block, "systemTag", systemtag)
    block = _set_attr(block, "displayName", name)
    block = _set_attr(block, "dataType", dtype)
    block = _set_attr(block, "arrayElements", str(array_elements))
    block = _set_attr(block, "signalAccessPath", str(access))
    block = _set_prop(block, "6100", _fresh_6100())             # fresh per-signal id
    block = _set_prop(block, "6103",                            # bit offset = byte*8
                      base64.b64encode(_s.pack("<I", access * 8)).decode("ascii"))
    new_body = body[:last.end()] + block + body[last.end():]
    cs["ECTDeviceBasic"] = _put_dev_xml(dev, xml[:m.start(2)] + new_body + xml[m.end(2):])
    # PdoEntryList: clone the highest entry `width` times (one BYTE entry per byte), each
    # with its OWN fresh GUID (key 6) + 6100 (key 7) + byte index @16 + NameMap.
    el = (cs.get("ProcessDataMgr", {}).get("PdoMgr", {}).get(_PDOMAP[direction], {})
          .get("0", {}).get("PdoEntryList"))
    if isinstance(el, dict) and el:
        for i in range(width):
            hi = max(el, key=lambda k: int(k))
            src = el[hi]
            new = {k: (bytes(v) if isinstance(v, (bytes, bytearray)) else
                       {kk: bytes(vv) for kk, vv in v.items()}) for k, v in src.items()}
            eb = bytearray(new["ECTPdoEntryBasic"])
            _s.pack_into("<I", eb, 16, access + i + 1)            # byte index (1-based)
            eb = bytearray(_pbag_set(eb, 6, str(uuid.uuid4()).upper()))   # per-byte GUID
            eb = bytearray(_pbag_set(eb, 7, _fresh_6100()))              # per-byte 6100
            new["ECTPdoEntryBasic"] = bytes(eb)
            enm = new.get("EntryNameMap")
            if isinstance(enm, dict) and isinstance(enm.get("NameMap"), (bytes, bytearray)):
                nm_name = name.encode("utf-16-le") + b"\x00\x00"     # name + null
                head = bytes(enm["NameMap"])[:8]                     # [u32 1][u32 0x0409 LCID]
                enm["NameMap"] = head + _s.pack("<I", len(nm_name)) + nm_name
            el[str(int(hi) + 1)] = new
    # SM data length += width
    sm = cs.get("ProcessDataMgr", {}).get("SmMgr", {}).get("SmMap", {}).get(str(_SM[direction]), {})
    smb = sm.get("ECTSyncManagerBasic") if isinstance(sm, dict) else None
    if isinstance(smb, (bytes, bytearray)) and len(smb) > 21:
        smb = bytearray(smb)
        smb[21] = min(255, smb[21] + width)
        sm["ECTSyncManagerBasic"] = bytes(smb)
    new_cfb = cfb_write.build(tree)
    return struct.pack("<I", len(new_cfb)) + new_cfb
