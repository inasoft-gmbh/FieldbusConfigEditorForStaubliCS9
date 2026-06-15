"""PROFINET BLOB module compiler — add a module to the SyCon project blob so SyCon
opens it AND shows the module.

Proven end-to-end (user-confirmed in SyCon, 2026-06-14). Per module the OLE2/CFB blob
needs, on top of the nxd ([[nxd-dbm-format]]):
  1. the device XML in `PNIODeviceDataModelBasic` gains a `<Module moduleAddress="Slot N">
     …<Module moduleAddress="Subslot 1"><Signal …></Module></Module>` block, inserted
     after the head (Slot 0) module; the u32 length prefix before that XML is updated.
  2. a full storage subtree `STLModuleMap/<slot>/…` (PNIOModuleBasic/DeviceBasic,
     STLSubModuleMap/1/PNIOSubModuleBasic/DeviceBasic, STL{Input,Output}DataVec/0/
     PNIODataBasic/DeviceBasic) AND the EMPTY storages (STLRecordParamDataMap,
     STLOutputDataVec, BitDescVec) — miss an empty storage and SyCon silently shows the
     module empty / not at all (cfb_write.read_tree keeps empty storages; listdir does not).
The CFB is then rebuilt with cfb_write.build (a from-scratch CFB the strict SyCon reader
accepts). The blob can't be byte-exact (SyCon randomises instance GUIDs every save), so
this clones a verified per-module template; verification is opening in SyCon."""
from __future__ import annotations
import base64
import copy
import io
import re
import struct
import uuid

import olefile

from . import sycon, cfb_write

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _replace_in_streams(node, a: bytes, b: bytes):
    for k, v in node.items():
        if isinstance(v, dict):
            _replace_in_streams(v, a, b)
        else:
            node[k] = v.replace(a, b)


def fresh_guids(block: str, subtree: dict):
    """Give the cloned module fresh, internally-consistent instance GUIDs so multiple
    instances of the same module type don't collide. Each template GUID appears as a
    string in the device-XML block + one stream, and as base64(bytes_le) in the block's
    Property 6100. GUID strings are a fixed 36 chars, so stream replacement is in-place
    (no resize). Returns (new_block, new_subtree)."""
    subtree = copy.deepcopy(subtree)
    for old in dict.fromkeys(_GUID.findall(block)):           # unique, order-preserving
        new = str(uuid.uuid4())
        ob = base64.b64encode(uuid.UUID(old).bytes_le).decode()
        nb = base64.b64encode(uuid.UUID(new).bytes_le).decode()
        block = block.replace(old, new).replace(ob, nb)
        _replace_in_streams(subtree, old.encode("utf-16-le"), new.encode("utf-16-le"))
    return block, subtree


def device_lenprefix(d: bytes) -> int:
    """Offset of the u32 length prefix right before the device XML. The device XML always
    starts with the head module "<Module …" (Slot 0), so anchor on the first "<Module"
    whose preceding u32 == the bytes from there to the end. Robust against the property-
    bag header before it (u32s + small strings, with stray "<" bytes) — the old
    u32==remaining + "<\\x00" heuristic could lock onto an earlier coincidence as the XML
    length changed across a rebuild chain (the robot's crash)."""
    # 1) the head module "<Module" whose preceding u32 == bytes-to-end (the clean case)
    needle = "<Module".encode("utf-16-le")        # b"<\x00M\x00o\x00d\x00u\x00l\x00e\x00"
    pos = d.find(needle)
    while pos != -1:
        if pos >= 4 and struct.unpack_from("<I", d, pos - 4)[0] == len(d) - pos:
            return pos - 4
        pos = d.find(needle, pos + 1)
    # 2) any "<" + letter with u32 == remaining (a non-"<Module" root)
    for i in range(len(d) - 7):
        if (d[i + 4:i + 6] == b"<\x00" and d[i + 6] != 0 and d[i + 7] == 0
                and struct.unpack_from("<I", d, i)[0] == len(d) - (i + 4)):
            return i
    # 3) SELF-HEAL: the property-bag header never contains "<" + letter (only the XML
    # does), so the FIRST "<" + letter is the XML start even when the stored length
    # prefix is wrong (a file an older build corrupted). The XML runs to the end of the
    # stream, so the next write recomputes a correct prefix and heals the file.
    for i in range(len(d) - 7):
        if d[i + 4:i + 6] == b"<\x00" and 65 <= d[i + 6] <= 122 and d[i + 7] == 0:
            return i
    # 4) EMPTY device XML: a device with no head (Slot 0) module — deleting every user
    # module leaves header + u32(L) + only whitespace (a leftover "\n"), so there is no
    # "<" at all. The prefix is the u32 that equals the remaining length whose content is
    # whitespace/empty; the next add then re-builds the XML from scratch.
    for i in range(len(d) - 3):
        if struct.unpack_from("<I", d, i)[0] == len(d) - (i + 4):
            try:
                if d[i + 4:].decode("utf-16-le").strip("\r\n\t \x00") == "":
                    return i
            except UnicodeDecodeError:
                pass
    raise ValueError(f"device-XML length prefix not found (stream {len(d)} B, "
                     "no '<'+letter — not a device-model stream)")


def extract_module_block(device_xml_text: str, slot: int) -> str:
    """The balanced `<Module … moduleAddress="Slot N"> … </Module>` block (with its
    leading whitespace) from a device XML string."""
    m = re.search(r'\n?\t*<Module\b[^>]*moduleAddress="Slot %d"' % slot, device_xml_text)
    if not m:
        raise ValueError(f"no Slot {slot} module block")
    depth = 1
    for mm in re.finditer(r"<(/?)Module\b", device_xml_text[m.end():]):
        depth += -1 if mm.group(1) else 1
        if depth == 0:
            end = device_xml_text.index(">", m.end() + mm.end()) + 1
            return device_xml_text[m.start():end]
    raise ValueError("unbalanced module block")


def _module_dir_size(block: str):
    """(direction, size_bytes) of a device-XML module block. Direction from the
    moduleType name (Eingang/Input -> input, else output), size from its 'N Byte'
    prefix — robust even for an emptied module (no signalType to read)."""
    mt = re.search(r'moduleType="([^"]*)"', block)
    name = mt.group(1) if mt else ""
    if "Eingang" in name or "Input" in name:
        direction = "input"
    elif "Ausgang" in name or "Output" in name:
        direction = "output"
    else:                                         # fall back to the signalType (inverted)
        st = re.search(r'signalType="([^"]*)"', block)
        direction = "input" if (st and st.group(1) == "output") else "output"
    msz = re.match(r"\s*(\d+)\s*Byte", name)
    return direction, (int(msz.group(1)) if msz else 0)


def _read_blob(path_or_xml, is_file):
    xml = open(path_or_xml, encoding="utf-8", errors="replace").read() if is_file else path_or_xml
    b = sycon.blob_from_xml(xml)
    return olefile.OleFileIO(io.BytesIO(b[4:]))


def signal_block(name, signal_type, global_byte, mod_byte, bit, dtype, arr=1, uid=None):
    """One device-XML <Signal> block (byte-exact vs SyCon, verified on sample 13). The
    signalAccessPath is the GLOBAL byte(.bit) within the direction's image; Property 6103
    is the MODULE-relative bit offset (mod_byte*8 + bit). `uid` reuses an existing
    systemTag (it must travel with the signal — [[systemtag-must-travel]]); only a brand-
    new signal gets a fresh UUID."""
    g = uid or str(uuid.uuid4())
    b64 = base64.b64encode(uuid.UUID(g).bytes_le).decode()
    ap = f"{global_byte}.{bit}" if dtype == "bit" else f"{global_byte}"
    off = base64.b64encode(struct.pack("<I", mod_byte * 8 + bit)).decode()
    return (f'<Signal systemTag="{g}" displayName="{name}" signalType="{signal_type}" '
            f'signalAccessPath="{ap}" dataType="{dtype}" arrayElements="{arr}" opc="1">'
            f'\r\n\t\t\t<Property id="6100" type="8" value="{b64}"/>'
            f'\r\n\t\t\t<Property id="6103" type="19" value="{off}"/>'
            f'\r\n\t\t</Signal>')


def set_block_signals(block, signals, signal_type, global_start):
    """Replace a module block's default single <Signal> with the user's `signals`
    (list of dicts: name, dtype, byte [module-relative], bit, arr). The bytes of a
    signal must lie within the module (caller's fit-check). SyCon-byte-exact."""
    inner = "\r\n\t\t".join(
        signal_block(s["name"], signal_type, global_start + s["byte"], s["byte"],
                     s.get("bit", 0), s["dtype"], s.get("arr", 1), s.get("uid"))
        for s in signals)
    if "<Signal" in block:                          # replace the existing signal(s)
        a = block.index("<Signal")
        b = block.rindex("</Signal>") + len("</Signal>")
        return block[:a] + inner + block[b:]
    if not inner:                                   # empty module, nothing to add
        return block
    # EMPTY module (its signals were deleted): insert before the Subslot's </Module>,
    # right after its last <Property/> — the indent before </Module> becomes the
    # trailing separator, mirroring SyCon's "<Property…/><Signal…>…</Signal>\r\n\t</Module>".
    pos = block.index("</Module>")
    while pos > 0 and block[pos - 1] in "\r\n\t ":
        pos -= 1                                     # back up over the indent
    return block[:pos] + inner + block[pos:]


def load_template(template_xml_path, src_slot=1):
    """Capture a reusable per-module template from a SyCon-saved blob: the full
    STLModuleMap/<src_slot> storage subtree (empty storages included), its device-XML
    `<Module>` block, and the block's current signal displayName (so callers can relabel)."""
    ole = _read_blob(template_xml_path, True)
    tree = cfb_write.read_tree(ole)
    dev = ole.openstream("PNIODeviceDataModelBasic").read()
    ole.close()
    block = extract_module_block(dev[device_lenprefix(dev) + 4:].decode("utf-16-le"), src_slot)
    sig = re.search(r'<Signal\b[^>]*\bdisplayName="([^"]*)"', block)
    return {"subtree": tree["STLModuleMap"][str(src_slot)], "block": block,
            "src_slot": src_slot, "signal_label": sig.group(1) if sig else None}


def add_module_to_blob(base_xml_text: str, template: dict, slot: int,
                       signal_label: str | None = None, signals=None,
                       direction: str = "input") -> str:
    """Return new project XML (BinData replaced) with the template module added at `slot`,
    with fresh instance GUIDs (multi-instance safe). If `signals` is given (a list of
    {name,dtype,byte,bit,arr}) the module's default whole-size signal is replaced by those
    user signals (multi-signal module); otherwise the default single signal is kept and
    `signal_label` sets its displayName."""
    ole = _read_blob(base_xml_text, False)
    tree = cfb_write.read_tree(ole)
    dev = ole.openstream("PNIODeviceDataModelBasic").read()
    ole.close()

    block, subtree = fresh_guids(template["block"], template["subtree"])
    if slot != template["src_slot"]:
        block = block.replace(f'moduleAddress="Slot {template["src_slot"]}"',
                              f'moduleAddress="Slot {slot}"')
    if signal_label and template.get("signal_label"):
        block = block.replace(f'displayName="{template["signal_label"]}"',
                              f'displayName="{signal_label}"', 1)

    lp = device_lenprefix(dev)
    main = dev[lp + 4:].decode("utf-16-le")

    # multi-module CONTEXT (proven on the 7-module interleaved sample):
    #   PNIOModuleBasic @6 = the OVERALL module index (1-based, across all directions).
    #   PNIOSubModuleBasic field (prop-id anchor 0x12 <u32> 0x13) = the cumulative INPUT
    #     bytes added before this module — for EVERY module, regardless of its direction
    #     (it tracks the input-area size at this point). Both are 1 / 0 for the first
    #     module, matching the clean templates.
    # Count by MODULE (size from moduleType), NOT by signal arrayElements — a module with
    # custom/partial signals has sum(arrayElements) != its size, which would mis-place the
    # NEXT module's global offset.
    existing = []
    for mb in re.finditer(r'moduleAddress="Slot (\d+)"', main):
        sl = int(mb.group(1))
        if sl == 0:
            continue
        existing.append(_module_dir_size(extract_module_block(main, sl)))
    overall_index = len(existing) + 1
    input_before = sum(sz for d, sz in existing if d == "input")
    output_before = sum(sz for d, sz in existing if d == "output")
    pm = bytearray(subtree["PNIOModuleBasic"])
    struct.pack_into("<H", pm, 6, overall_index)             # overall module index (1-based)
    subtree["PNIOModuleBasic"] = bytes(pm)
    ps = bytearray(subtree["STLSubModuleMap"]["1"]["PNIOSubModuleBasic"])
    mm = re.search(b"\x12\x00\x00\x00(....)\x13\x00\x00\x00(....)", ps, re.S)  # 0x12<inBefore>0x13<outBefore>
    if mm:
        struct.pack_into("<I", ps, mm.start(1), input_before)
        struct.pack_into("<I", ps, mm.start(2), output_before)
        subtree["STLSubModuleMap"]["1"]["PNIOSubModuleBasic"] = bytes(ps)

    # signalAccessPath is the GLOBAL byte within the direction, so it must be re-based on
    # the bytes of the same-direction modules already present (SyCon does this — sample 06
    # slot2 = ap 8, sample 11 slot3 = ap 21). signals is None -> keep the template's
    # default whole-module signal (just re-address it); a list (incl. EMPTY []) -> use
    # exactly those signals, so a brand-new slot can start with NO signal (empty bytes).
    global_start = input_before if direction == "input" else output_before
    if signals is not None:
        sig_type = "output" if direction == "input" else "input"   # inverted naming
        block = set_block_signals(block, signals, sig_type, global_start)
    else:
        block = _readdress_block(block, global_start)

    if "<Module" in main:
        ins = main.rstrip().rfind("</Module>") + len("</Module>")  # after the last module
        combined = main[:ins] + block + main[ins:]
    else:
        # empty / head-less device XML (a config built by 'New config' has no Slot 0 head,
        # so deleting all modules leaves only leftover whitespace) -> this module IS the
        # whole XML; drop any leading whitespace so it starts exactly with "<Module".
        combined = block.lstrip("\r\n\t ")
    new_main = combined.encode("utf-16-le")
    tree["PNIODeviceDataModelBasic"] = dev[:lp] + struct.pack("<I", len(new_main)) + new_main
    tree.setdefault("STLModuleMap", {})[str(slot)] = subtree

    cfb = cfb_write.build(tree)
    hexlo = (struct.pack("<I", len(cfb)) + cfb).hex()
    return re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                  lambda m: m.group(1) + hexlo + m.group(2), base_xml_text, count=1, flags=re.S)


# One verified SIZE-1 blob template per direction (from the saved samples); any size is
# generated by patching (sizes encode as same-length names + 3 numeric fields, decoded
# from the 1-Byte vs 4-Byte diff). The module catalog itself comes from the GSDML.
import os as _os  # noqa: E402

_DOCS = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
                      "docs", "blob_samples")
_BASE_TEMPLATE = {                       # direction -> (sample, slot) of a clean 1-Byte module
    "input": ("01_1mod_label.xml", 1),
    "output": ("12_1mod_1byte_out.xml", 1),
}


def _swap_size_names(data: bytes, size: int) -> bytes:
    """Replace the size digit in every length-prefixed utf-16 name field that contains
    "1byte"/"1 Byte" ("1byteinput", "1 Byte Eingang", "Module_1byteinput_Name", …) AND
    update that field's u32 byte-length prefix. Needed because a 2-digit size lengthens
    the names (a naive byte-replace would leave the prefixes stale and SyCon would
    mis-read them). Fields are `[u32 byte_len][utf-16 chars][NUL]`."""
    markers = ("1byte", "1 Byte")
    out = bytearray(data)
    hits = []
    i = 0
    while i < len(out) - 4:
        L = struct.unpack_from("<I", out, i)[0]
        if 4 <= L <= 200 and L % 2 == 0 and i + 4 + L <= len(out) \
                and out[i + 4 + L - 2:i + 4 + L] == b"\x00\x00":
            try:
                txt = out[i + 4:i + 4 + L - 2].decode("utf-16-le")
            except UnicodeDecodeError:
                txt = ""
            if any(m in txt for m in markers):
                hits.append((i, L, txt))
                i += 4 + L
                continue
        i += 1
    for pos, L, txt in reversed(hits):                 # back-to-front keeps offsets valid
        new = (txt.replace("1byte", f"{size}byte")
               .replace("1 Byte", f"{size} Byte")).encode("utf-16-le") + b"\x00\x00"
        out[pos:pos + 4 + L] = struct.pack("<I", len(new)) + new
    return bytes(out)


def _patch_size(subtree: dict, block: str, size: int, module_ident: int,
                submodule_ident: int) -> str:
    """Turn a 1-Byte template module into an N-Byte one (same module direction). Sizes
    differ only by: the digit in the names "1byte"/"1 Byte" (same length), the module/
    submodule GSDML idents (u32 @ PNIO[Sub]ModuleBasic+42), the data length (u16 @
    PNIODataBasic+120), and the device-XML arrayElements + display. Verified byte-exact
    (masked) against the 4-Byte sample."""
    # numeric fields, located by ROBUST ANCHORS so they work for input AND output (the
    # "output" names are longer than "input", shifting fixed offsets) and any size:
    #   module/submodule GSDML ident: u32 between property-ids 0x03 and 0x04
    #   data length: the u16 right before the constant property 0x05 (=00000005)
    sm = subtree["STLSubModuleMap"]["1"]          # In/Out both exist; one is the empty storage
    datavec = "STLInputDataVec" if "0" in sm.get("STLInputDataVec", {}) else "STLOutputDataVec"

    def set_ident(path, val):
        cur = subtree
        for p in path[:-1]:
            cur = cur[p]
        d = bytearray(cur[path[-1]])
        mm = re.search(b"\x03\x00\x00\x00(....)\x04\x00\x00\x00", d, re.S)
        struct.pack_into("<I", d, mm.start(1), val)
        cur[path[-1]] = bytes(d)
    set_ident(("PNIOModuleBasic",), module_ident)
    set_ident(("STLSubModuleMap", "1", "PNIOSubModuleBasic"), submodule_ident)
    pd = bytearray(sm[datavec]["0"]["PNIODataBasic"])
    struct.pack_into("<H", pd, pd.find(b"\x05\x00\x00\x00") - 2, size)
    sm[datavec]["0"]["PNIODataBasic"] = bytes(pd)

    if size != 1:
        def walk(node):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v)
                else:
                    node[k] = _swap_size_names(v, size)
        walk(subtree)
        block = (block.replace("1 Byte", f"{size} Byte")
                 .replace('arrayElements="1"', f'arrayElements="{size}"'))
    # NOTE: PNIOModuleBasic @6 (same-direction module index, 1-based) and
    # PNIOSubModuleBasic DPM-offset field are CONTEXT (multi-module) fields, not size
    # fields — they are 1 / 0 for a single (or first-of-direction) module = the template
    # defaults, so nothing to do here. Multiple same-direction modules are handled by the
    # caller (add_module_to_blob) which knows the running offset/index.
    return block


_DT_BITS = {"bit": 1, "byte": 8, "signed8": 8, "unsigned8": 8,
            "word": 16, "signed16": 16, "unsigned16": 16,
            "dword": 32, "signed32": 32, "unsigned32": 32, "real32": 32}


def write_module_signals(base_xml, slot, signals, direction, global_start):
    """Rewrite ONE module's <Signal> blocks in the device XML (nxd + per-slot CFB are
    untouched for signal edits) and return the new project XML. `signals` = list of
    {name,dtype,byte,bit,arr} (module-relative). Caller ensures they fit in the module."""
    ole = _read_blob(base_xml, False)
    tree = cfb_write.read_tree(ole)
    dev = ole.openstream("PNIODeviceDataModelBasic").read()
    ole.close()
    lp = device_lenprefix(dev)
    main = dev[lp + 4:].decode("utf-16-le")
    block = extract_module_block(main, slot)
    sig_type = "output" if direction == "input" else "input"
    new_block = set_block_signals(block, signals, sig_type, global_start)
    new_main = (main[:main.index(block)] + new_block
                + main[main.index(block) + len(block):]).encode("utf-16-le")
    tree["PNIODeviceDataModelBasic"] = dev[:lp] + struct.pack("<I", len(new_main)) + new_main
    cfb = cfb_write.build(tree)
    hexlo = (struct.pack("<I", len(cfb)) + cfb).hex()
    return re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                  lambda m: m.group(1) + hexlo + m.group(2), base_xml, count=1, flags=re.S)


def parse_modules(base_xml):
    """Parse the PROFINET user modules + their signals from the project's device XML.
    Returns a list of dicts: {slot, module_type, direction, global_start, size, signals}
    where each signal is {name, dtype, byte, bit, arr} with byte/bit MODULE-relative
    (byte = 6103 offset // 8). direction: input (signalType output) / output (input)."""
    ole = _read_blob(base_xml, False)
    dev = ole.openstream("PNIODeviceDataModelBasic").read()
    ole.close()
    main = dev[device_lenprefix(dev) + 4:].decode("utf-16-le")
    out = []
    before = {"input": 0, "output": 0}             # cumulative bytes per direction
    for mb in re.finditer(r'<Module\b[^>]*moduleAddress="Slot (\d+)"', main):
        slot = int(mb.group(1))
        if slot == 0:
            continue
        block = extract_module_block(main, slot)
        mtype = re.search(r'moduleType="([^"]*)"', block).group(1)
        # direction from the moduleType NAME (Eingang/Ausgang) — robust even when the
        # module has NO signals (emptied), where a signalType-based guess wrongly
        # defaulted to "input" and the module jumped to the Eingänge table.
        direction, _msz = _module_dir_size(block)
        sigs = []
        for sg in re.finditer(r'<Signal\b([^>]*)>(.*?)</Signal>', block, re.S):
            a, body = sg.group(1), sg.group(2)

            def gv(k, s=a):
                m = re.search(k + r'="([^"]*)"', s)
                return m.group(1) if m else None
            ap = gv("signalAccessPath")
            bit = int(ap.split(".")[1]) if "." in ap else 0
            off = struct.unpack("<I", base64.b64decode(
                re.search(r'id="6103"[^>]*value="([^"]*)"', body).group(1)))[0]
            sigs.append(dict(name=gv("displayName"), dtype=gv("dataType"),
                             byte=off // 8, bit=bit, arr=int(gv("arrayElements") or 1),
                             uid=gv("systemTag")))
        # the module's true byte size comes from its type ("8 Byte Eingang" -> 8), so
        # trailing gaps (signals don't fill the module) are represented correctly.
        msize = re.match(r"\s*(\d+)\s*Byte", mtype)
        size = int(msize.group(1)) if msize else max(
            (s["byte"] + (_DT_BITS[s["dtype"]] * s["arr"] + 7) // 8 for s in sigs), default=0)
        # global_start = cumulative SAME-direction bytes before this module (the device-XML
        # = layout order). Computed from sizes, NOT from a signal — so an EMPTY module
        # keeps its correct position (a signal-derived start would be 0 and jump it up).
        gstart = before[direction]
        before[direction] += size
        out.append(dict(slot=slot, module_type=mtype, direction=direction,
                        global_start=gstart, size=size, signals=sigs))
    return out


def add_catalog_module(base_xml, base_nxd, module_ident, slot, signal_label=None,
                       signals=None):
    """Add a GSDML catalog module (any size) to BOTH the blob and the main.nxd. Returns
    (new_xml, new_nxd_bytes). `module_ident` = GSDML ModuleIdentNumber. `signals` (a list
    of {name,dtype,byte,bit,arr}, module-relative) subdivides the module into several
    signals; without it the module keeps one whole-size signal labelled `signal_label`."""
    from . import nxd_dbm, gsdml
    mod = next((m for m in gsdml.catalog() if m.module_ident == module_ident), None)
    if mod is None:
        raise ValueError(f"module ident {module_ident:#x} not in GSDML catalog")
    sample, src = _BASE_TEMPLATE[mod.direction]
    tmpl = load_template(_os.path.join(_DOCS, sample), src)
    tmpl["block"] = _patch_size(tmpl["subtree"], tmpl["block"], mod.size,
                                module_ident, mod.submodule_ident)
    label = signal_label or ("Eingänge" if mod.direction == "input" else "Ausgänge")
    new_xml = add_module_to_blob(base_xml, tmpl, slot, label,
                                 signals=signals, direction=mod.direction)
    n = nxd_dbm.parse(base_nxd)
    nxd_dbm.add_module(n, slot, module_ident, mod.submodule_ident, mod.size, mod.direction)
    return new_xml, nxd_dbm.serialize(n)


def _readdress_block(block: str, global_start: int) -> str:
    """Rewrite every <Signal>'s signalAccessPath (the GLOBAL byte[.bit] within the
    direction) from its module-relative Property 6103, KEEPING the systemTag + 6100/6103
    intact — used when deleting a module shifts the following same-direction modules'
    global addresses (the UID must travel with the signal: [[systemtag-must-travel]])."""
    def fix(sg):
        body = sg.group(0)
        off = struct.unpack("<I", base64.b64decode(
            re.search(r'id="6103"[^>]*value="([^"]*)"', body).group(1)))[0]
        byte, bit = off // 8, off % 8
        dtype = re.search(r'dataType="([^"]*)"', body).group(1)
        ap = f"{global_start + byte}.{bit}" if dtype == "bit" else f"{global_start + byte}"
        return re.sub(r'signalAccessPath="[^"]*"', f'signalAccessPath="{ap}"', body, count=1)
    return re.sub(r"<Signal\b.*?</Signal>", fix, block, flags=re.S)


def delete_module_from_blob(base_xml_text: str, slot: int) -> str:
    """Return new project XML with the `slot` module removed from the device XML AND its
    STLModuleMap/<slot> storage subtree dropped, then the multi-module CONTEXT recomputed
    for every remaining module (overall index @6, the PNIOSubModuleBasic input/output
    bytes-before, and the device-XML signalAccessPath of later same-direction modules,
    which shift when an earlier module is removed). Inverse of add_module_to_blob."""
    ole = _read_blob(base_xml_text, False)
    tree = cfb_write.read_tree(ole)
    dev = ole.openstream("PNIODeviceDataModelBasic").read()
    ole.close()
    lp = device_lenprefix(dev)
    main = dev[lp + 4:].decode("utf-16-le")

    target = extract_module_block(main, slot)
    main = main.replace(target, "", 1)
    tree.get("STLModuleMap", {}).pop(str(slot), None)

    in_before = out_before = 0
    index = 0
    for s in [int(m.group(1)) for m in
              re.finditer(r'moduleAddress="Slot (\d+)"', main) if int(m.group(1)) != 0]:
        index += 1
        block = extract_module_block(main, s)
        direction, size = _module_dir_size(block)
        gstart = in_before if direction == "input" else out_before

        new_block = _readdress_block(block, gstart)
        if new_block != block:
            main = main.replace(block, new_block, 1)

        sub = tree.get("STLModuleMap", {}).get(str(s))
        if sub:
            pm = bytearray(sub["PNIOModuleBasic"])
            struct.pack_into("<H", pm, 6, index)
            sub["PNIOModuleBasic"] = bytes(pm)
            ps = bytearray(sub["STLSubModuleMap"]["1"]["PNIOSubModuleBasic"])
            mm = re.search(b"\x12\x00\x00\x00(....)\x13\x00\x00\x00(....)", ps, re.S)
            if mm:
                struct.pack_into("<I", ps, mm.start(1), in_before)
                struct.pack_into("<I", ps, mm.start(2), out_before)
                sub["STLSubModuleMap"]["1"]["PNIOSubModuleBasic"] = bytes(ps)
        if direction == "input":
            in_before += size
        else:
            out_before += size

    new_main = main.encode("utf-16-le")
    tree["PNIODeviceDataModelBasic"] = dev[:lp] + struct.pack("<I", len(new_main)) + new_main
    cfb = cfb_write.build(tree)
    hexlo = (struct.pack("<I", len(cfb)) + cfb).hex()
    return re.sub(r"(<BinData[^>]*>).*?(</BinData>)",
                  lambda m: m.group(1) + hexlo + m.group(2), base_xml_text, count=1, flags=re.S)


def delete_catalog_module(base_xml, base_nxd, slot):
    """Remove the module in `slot` from BOTH the blob and the main.nxd. Returns
    (new_xml, new_nxd_bytes or None). Inverse of add_catalog_module."""
    from . import nxd_dbm
    new_xml = delete_module_from_blob(base_xml, slot)
    new_nxd = None
    if base_nxd is not None:
        n = nxd_dbm.parse(base_nxd)
        nxd_dbm.delete_module(n, slot)
        new_nxd = nxd_dbm.serialize(n)
    return new_xml, new_nxd


def _catalog_for(size, direction):
    from . import gsdml
    return next((m for m in gsdml.catalog()
                 if m.size == size and m.direction == direction), None)


def capture_modules(base_xml):
    """Ordered specs of the current user modules — the input to rebuild_modules. Each:
    {module_ident, slot, direction, size, signals (with their UIDs)}. Order = device-XML
    (= layout) order. Raises if a module's size/direction isn't in the GSDML catalog."""
    specs = []
    for m in parse_modules(base_xml):
        cm = _catalog_for(m["size"], m["direction"])
        if cm is None:
            raise ValueError(f"no GSDML module for {m['size']} B {m['direction']}")
        specs.append(dict(module_ident=cm.module_ident, slot=m["slot"],
                          direction=m["direction"], size=m["size"],
                          signals=[dict(s) for s in m["signals"]]))
    return specs


def rebuild_modules(base_xml, base_nxd, specs):
    """Re-create the whole user-module set in the given order/sizes/slots, keeping each
    signal's UID. Deletes ALL current user modules (down to the robot's own 0-module
    base, identity intact) then re-adds per `specs` — so it reuses ONLY the byte-exact
    add/delete compilers. Drives module reorder and module edit (size/slot). Returns
    (new_xml, new_nxd)."""
    xml, nxd = base_xml, base_nxd
    for slot in [m["slot"] for m in parse_modules(xml)]:
        xml, nxd = delete_catalog_module(xml, nxd, slot)
    for sp in specs:
        # pass the signal list as-is: [] keeps the module EMPTY (don't fall back to the
        # default whole-module signal); None would mean "default".
        sigs = sp.get("signals")
        xml, nxd = add_catalog_module(xml, nxd, sp["module_ident"], sp["slot"],
                                      signals=sigs if sigs is not None else None)
    return xml, nxd
