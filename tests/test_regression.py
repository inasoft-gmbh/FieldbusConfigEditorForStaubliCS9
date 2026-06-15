"""Golden-file regression tests for the SyCon-compatible compilers.

Every sample under docs/{nxd,blob}_samples was produced by SyCon itself and is the
ground truth. This test reproduces each one with our code and asserts it comes out
EXACTLY as SyCon's (byte-for-byte for the nxd; stream-for-stream for the CFB blob).
Run after ANY change — the parser/serializer/CFB code is shared across bus systems,
so a tweak for one can silently break another; this catches it.

    python tests/test_regression.py        # prints PASS/FAIL, exits nonzero on failure

Add a new variant by dropping the SyCon file into docs/nxd_samples (and/or
docs/blob_samples) and adding a line to NXD_COMPILE below."""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")
from fbconfig import nxd_dbm, cfb_write, sycon          # noqa: E402
import olefile                                            # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NXD = ROOT / "docs" / "nxd_samples"
BLOB = ROOT / "docs" / "blob_samples"
NXD_BASE = "00_0module.nxd"

# golden nxd -> (base, [add_module(slot, module_ident, submodule_ident, datalen, dir)])
# module/submodule idents are the GSDML values (Eingang 0x2/0x4..., Ausgang 0x3...).
NXD_COMPILE = [
    ("01_1mod_2byte_in.nxd",  [(1, 4, 3, 2, "input")]),
    ("02_1mod_1byte_out.nxd", [(1, 3, 2, 1, "output")]),
    ("03_2mod_in1_out2.nxd",  [(1, 2, 1, 1, "input"), (2, 3, 2, 1, "output")]),
    ("04_1mod_4byte_in.nxd",  [(1, 0x8, 0x7, 4, "input")]),
    ("05_1mod_8byte_in.nxd",  [(1, 0xa, 0x9, 8, "input")]),
    ("06_2mod_8in_64in.nxd",  [(1, 0xa, 0x9, 8, "input"), (2, 0x14, 0x13, 64, "input")]),
    ("07_1mod_16byte_in.nxd", [(1, 0xe, 0xd, 16, "input")]),
    ("08_1mod_32byte_in.nxd", [(1, 0x12, 0x11, 32, "input")]),
    ("09_1mod_64byte_in.nxd", [(1, 0x14, 0x13, 64, "input")]),
    ("10_2mod_12in_20in.nxd", [(1, 0xc, 0xb, 12, "input"), (2, 0x10, 0xf, 20, "input")]),
    ("11_7mod_mixed.nxd", [(1, 0x2, 0x1, 1, "input"), (2, 0x10, 0xf, 20, "input"),
                           (3, 0x12, 0x11, 32, "input"), (4, 0x5, 0x4, 2, "output"),
                           (5, 0x13, 0x12, 32, "output"), (6, 0xa, 0x9, 8, "input"),
                           (7, 0x3, 0x2, 1, "output")]),
]

# single-module blob byte-exact (masked) across all input sizes: (gsdml ident, sample)
BLOB_SIZES = [(0x2, "01_1mod_label.xml"), (0x8, "04_1mod_4byte_in.xml"),
              (0xa, "05_1mod_8byte_in.xml"), (0xe, "07_1mod_16byte_in.xml"),
              (0x12, "08_1mod_32byte_in.xml"), (0x14, "09_1mod_64byte_in.xml")]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  -> ' + detail) if detail and not ok else ''}")


def full_map(cfb_bytes):
    """Every storage AND stream (empty storages included) -> ('storage',None) or
    ('stream', bytes). olefile.listdir() omits empty storages, which SyCon needs."""
    ole = olefile.OleFileIO(io.BytesIO(cfb_bytes))
    out = {}

    def rec(entry, path):
        for kid in entry.kids:
            p = path + [kid.name]
            if kid.entry_type == 1:
                out["/".join(p)] = ("storage", None)
                rec(kid, p)
            else:
                out["/".join(p)] = ("stream", ole.openstream(p).read())
    rec(ole.root, [])
    ole.close()
    return out


print("== nxd parser round-trip (parse -> serialize == original) ==")
for f in sorted(NXD.glob("*.nxd")):
    d = f.read_bytes()
    out = nxd_dbm.serialize(nxd_dbm.parse(d))
    check(f"roundtrip {f.name}", out == d,
          "" if out == d else f"len {len(out)} vs {len(d)}")

print("== nxd module compiler (reproduce SyCon byte-for-byte) ==")
base = (NXD / NXD_BASE).read_bytes()
for name, mods in NXD_COMPILE:
    ref = (NXD / name).read_bytes()
    n = nxd_dbm.parse(base)
    for m in mods:
        nxd_dbm.add_module(n, *m)
    out = nxd_dbm.serialize(n)
    ok = out == ref
    detail = ""
    if not ok:
        diffs = [i for i in range(min(len(out), len(ref))) if out[i] != ref[i]]
        detail = f"len {len(out)}/{len(ref)} diffs {len(diffs)} first {diffs[:3]}"
    check(f"compile {name}", ok, detail)

print("== CFB writer round-trip (rebuild blob -> full tree identical, incl. storages) ==")
for f in sorted(BLOB.glob("*.xml")):
    b = sycon.blob_from_xml(f.read_text("utf-8", "replace"))
    orig = full_map(b[4:])
    ole = olefile.OleFileIO(io.BytesIO(b[4:]))
    rebuilt = cfb_write.build(cfb_write.read_tree(ole))
    ole.close()
    got = full_map(rebuilt)
    ok = orig == got
    detail = ""
    if not ok:
        miss = [k for k in orig if k not in got or orig[k] != got.get(k)]
        extra = [k for k in got if k not in orig]
        detail = f"{len(miss)} differ {miss[:2]}, {len(extra)} extra {extra[:2]}"
    check(f"cfb roundtrip {f.name}", ok, detail)

print("== blob module compiler (add a module -> full slot structure like SyCon) ==")
from fbconfig import blob_pn  # noqa: E402


def slot_paths(cfb_bytes, slot):
    return {k for k in full_map(cfb_bytes) if k.startswith(f"STLModuleMap/{slot}")}


tmpl = blob_pn.load_template(str(BLOB / "01_1mod_label.xml"), src_slot=1)
base_xml = (BLOB / "00_0module.xml").read_text("utf-8", "replace")
out_xml = blob_pn.add_module_to_blob(base_xml, tmpl, slot=1)
got = slot_paths(sycon.blob_from_xml(out_xml)[4:], 1)
want = slot_paths(sycon.blob_from_xml((BLOB / "01_1mod_label.xml").read_text("utf-8", "replace"))[4:], 1)
ok = got == want
check("blob add 1 module -> slot-1 structure complete", ok,
      "" if ok else f"missing {sorted(want - got)} extra {sorted(got - want)}")
# and the rebuilt blob must reopen cleanly with both slots
ole2 = olefile.OleFileIO(io.BytesIO(sycon.blob_from_xml(out_xml)[4:]))
both = sorted(s[1] for s in ole2.listdir() if "STLModuleMap" in s and s[-1] == "PNIOModuleBasic")
ole2.close()
check("blob add 1 module -> slots [0,1]", both == ["0", "1"], f"got {both}")

print("== catalog module add (blob + nxd together, fresh GUIDs) ==")
base_x = (BLOB / "00_0module.xml").read_text("utf-8", "replace")
base_n = (NXD / "00_0module.nxd").read_bytes()
x, nn = blob_pn.add_catalog_module(base_x, base_n, 0x2, 1, "MeinEingang")
x, nn = blob_pn.add_catalog_module(x, nn, 0x3, 2, "MeinAusgang")
check("catalog add -> nxd byte-exact vs SyCon sample03", nn == (NXD / "03_2mod_in1_out2.nxd").read_bytes())
cb = sycon.blob_from_xml(x)[4:]
o = olefile.OleFileIO(io.BytesIO(cb))
slots = sorted(s[1] for s in o.listdir() if "STLModuleMap" in s and s[-1] == "PNIOModuleBasic")
dmx = o.openstream("PNIODeviceDataModelBasic").read()
o.close()
mn = dmx[blob_pn.device_lenprefix(dmx) + 4:].decode("utf-16-le")
guids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", mn)
check("catalog add -> slots [0,1,2]", slots == ["0", "1", "2"], f"got {slots}")
check("catalog add -> custom labels in device XML",
      'displayName="MeinEingang"' in mn and 'displayName="MeinAusgang"' in mn)
check("catalog add -> all GUIDs unique (fresh)", len(guids) == len(set(guids)),
      f"{len(guids)} guids {len(set(guids))} unique")

print("== size-patch (4-Byte module blob == SyCon sample, GUID-masked) ==")
_GMASK = re.compile(rb"(?:[0-9a-f]\x00){8}-\x00(?:[0-9a-f]\x00){4}-\x00(?:[0-9a-f]\x00){4}-"
                    rb"\x00(?:[0-9a-f]\x00){4}-\x00(?:[0-9a-f]\x00){12}")


def slot1_streams(cfb):
    o = olefile.OleFileIO(io.BytesIO(cfb))
    d = {"/".join(p[2:]): o.openstream(p).read()
         for p in o.listdir() if p[:2] == ["STLModuleMap", "1"]}
    o.close()
    return d


for ident, sample in BLOB_SIZES:
    xs, _ = blob_pn.add_catalog_module((BLOB / "00_0module.xml").read_text("utf-8", "replace"),
                                       (NXD / "00_0module.nxd").read_bytes(), ident, 1, "Eingänge")
    ms = slot1_streams(sycon.blob_from_xml(xs)[4:])
    rs = slot1_streams(sycon.blob_from_xml((BLOB / sample).read_text("utf-8", "replace"))[4:])
    okk = all(_GMASK.sub(b"G" * 72, ms[k]) == _GMASK.sub(b"G" * 72, rs.get(k, b"")) for k in ms)
    check(f"size-patch blob {sample} == SyCon (masked)", okk,
          "" if okk else f"mismatch {[k for k in ms if _GMASK.sub(b'G'*72, ms[k]) != _GMASK.sub(b'G'*72, rs.get(k, b''))]}")

print("== multi-same-direction blob (8+64 input == SyCon sample 06) ==")
xx, nn6 = blob_pn.add_catalog_module((BLOB / "00_0module.xml").read_text("utf-8", "replace"),
                                     (NXD / "00_0module.nxd").read_bytes(), 0xa, 1, "Eingänge")
xx, nn6 = blob_pn.add_catalog_module(xx, nn6, 0x14, 2, "Eingänge")
for sl in (1, 2):
    def s_of(cfb, slot):
        o = olefile.OleFileIO(io.BytesIO(cfb))
        d = {"/".join(p[2:]): o.openstream(p).read()
             for p in o.listdir() if p[:2] == ["STLModuleMap", str(slot)]}
        o.close()
        return d
    ms = s_of(sycon.blob_from_xml(xx)[4:], sl)
    rs = s_of(sycon.blob_from_xml((BLOB / "06_2mod_8in_64in.xml").read_text("utf-8", "replace"))[4:], sl)
    okk = all(_GMASK.sub(b"G" * 72, ms[k]) == _GMASK.sub(b"G" * 72, rs.get(k, b"")) for k in ms)
    check(f"multi-same-dir blob slot {sl} == SyCon (masked)", okk,
          "" if okk else f"mismatch {[k for k in ms if _GMASK.sub(b'G'*72, ms[k]) != _GMASK.sub(b'G'*72, rs.get(k, b''))]}")
check("multi-same-dir nxd == SyCon sample06", nn6 == (NXD / "06_2mod_8in_64in.nxd").read_bytes())

print("== full 7-module interleaved (add_catalog_module: blob + nxd vs sample 11) ==")
from fbconfig import gsdml  # noqa: E402
_cat = {m.name: m for m in gsdml.catalog()}
_plan = [("1 Byte Eingang", 1), ("20 Byte Eingang", 2), ("32 Byte Eingang", 3),
         ("2 Byte Ausgang", 4), ("32 Byte Ausgang", 5), ("8 Byte Eingang", 6),
         ("1 Byte Ausgang", 7)]
xc = (BLOB / "00_0module.xml").read_text("utf-8", "replace")
ncc = (NXD / "00_0module.nxd").read_bytes()
for nm, sl in _plan:
    xc, ncc = blob_pn.add_catalog_module(xc, ncc, _cat[nm].module_ident, sl)
check("7-module nxd byte-exact vs SyCon sample 11", ncc == (NXD / "11_7mod_mixed.nxd").read_bytes())
ref_cfb = sycon.blob_from_xml((BLOB / "11_7mod_mixed.xml").read_text("utf-8", "replace"))[4:]
my_cfb = sycon.blob_from_xml(xc)[4:]


def slot_streams(cfb, slot):
    o = olefile.OleFileIO(io.BytesIO(cfb))
    d = {"/".join(p[2:]): o.openstream(p).read()
         for p in o.listdir() if p[:2] == ["STLModuleMap", str(slot)]}
    o.close()
    return d


blob_ok = all(
    _GMASK.sub(b"G" * 72, ms[k]) == _GMASK.sub(b"G" * 72, rs.get(k, b""))
    for sl in range(1, 8)
    for ms, rs in [(slot_streams(my_cfb, sl), slot_streams(ref_cfb, sl))]
    for k in ms)
check("7-module blob all slots == SyCon (masked)", blob_ok)

print("== device-XML signalAccessPath (global re-basing == SyCon) ==")


def device_aps(xml):
    """Ordered (slot, signalAccessPath) of the user modules in a project's device XML."""
    cfb = sycon.blob_from_xml(xml)[4:]
    o = olefile.OleFileIO(io.BytesIO(cfb))
    dev = o.openstream("PNIODeviceDataModelBasic").read()
    o.close()
    main = dev[blob_pn.device_lenprefix(dev) + 4:].decode("utf-16-le")
    out = []
    for mb in re.finditer(r'moduleAddress="Slot (\d+)"', main):
        sl = int(mb.group(1))
        if sl == 0:
            continue
        blk = blob_pn.extract_module_block(main, sl)
        for sg in re.finditer(r"<Signal\b[^>]*>", blk):
            ap = re.search(r'signalAccessPath="([^"]*)"', sg.group(0))
            out.append((sl, ap.group(1)))
    return out


# rebuild 06 (8+64 input) and 11 (7 interleaved); the default signal's accessPath must be
# re-based to the global byte exactly as SyCon writes it (latent until checked here).
x6, n6 = blob_pn.add_catalog_module((BLOB / "00_0module.xml").read_text("utf-8", "replace"),
                                    (NXD / "00_0module.nxd").read_bytes(), 0xa, 1, "Eingänge")
x6, n6 = blob_pn.add_catalog_module(x6, n6, 0x14, 2, "Eingänge")
check("device-XML accessPath 06 == SyCon",
      device_aps(x6) == device_aps((BLOB / "06_2mod_8in_64in.xml").read_text("utf-8", "replace")))
x11, n11 = (BLOB / "00_0module.xml").read_text("utf-8", "replace"), (NXD / "00_0module.nxd").read_bytes()
for nm, sl in _plan:
    x11, n11 = blob_pn.add_catalog_module(x11, n11, _cat[nm].module_ident, sl)
check("device-XML accessPath 11 == SyCon",
      device_aps(x11) == device_aps((BLOB / "11_7mod_mixed.xml").read_text("utf-8", "replace")))

print("== nxd delete (inverse of add: round-trip + position-invariant) ==")
_dn_base = (NXD / "00_0module.nxd").read_bytes()
_DA, _DB, _DC = ("8 Byte Eingang", "1 Byte Ausgang", "2 Byte Eingang")


def _nxd_build(plan):
    n = nxd_dbm.parse(_dn_base)
    for slot, nm in plan:
        m = _cat[nm]
        nxd_dbm.add_module(n, slot, m.module_ident, m.submodule_ident, m.size, m.direction)
    return nxd_dbm.serialize(n)


def _nxd_add_del(plan, delslot):
    n = nxd_dbm.parse(_dn_base)
    for slot, nm in plan:
        m = _cat[nm]
        nxd_dbm.add_module(n, slot, m.module_ident, m.submodule_ident, m.size, m.direction)
    nxd_dbm.delete_module(n, delslot)
    return nxd_dbm.serialize(n)


check("nxd add 1 + delete 1 == base", _nxd_add_del([(1, _DA)], 1) == _dn_base)
_full = [(1, _DA), (2, _DB), (3, _DC), (4, "32 Byte Ausgang"), (5, "64 Byte Eingang")]
_pos_ok = all(
    _nxd_add_del(_full, _full[i][0]) == _nxd_build([p for j, p in enumerate(_full) if j != i])
    for i in range(len(_full)))
check("nxd delete any position == fresh build of the rest", _pos_ok)

print("== blob delete (inverse of add: streams + accessPath == fresh build) ==")


def _blob_build(plan):
    x, n = (BLOB / "00_0module.xml").read_text("utf-8", "replace"), _dn_base
    for slot, nm in plan:
        x, n = blob_pn.add_catalog_module(x, n, _cat[nm].module_ident, slot)
    return x, n


def _blob_streams(xml):
    # all CFB streams/storages, GUID-masked, EXCEPT the device-XML streams (their random
    # systemTag + base64 6100 GUIDs aren't the utf-16 GUID pattern; verified via accessPath)
    cfb = sycon.blob_from_xml(xml)[4:]
    return {k: (_GMASK.sub(b"G" * 72, v[1]) if v[0] == "stream" else None)
            for k, v in full_map(cfb).items() if "PNIODeviceDataModel" not in k}


_xb, _nb = _blob_build(_full)
_xd, _nd = blob_pn.delete_catalog_module(_xb, _nb, 3)        # delete a middle input module
_xf, _nf = _blob_build([p for p in _full if p[0] != 3])
check("blob delete nxd == fresh build", _nd == _nf)
check("blob delete streams (masked) == fresh build", _blob_streams(_xd) == _blob_streams(_xf))
check("blob delete device-XML accessPath == fresh build", device_aps(_xd) == device_aps(_xf))

def _catg(size, direction):
    return next(m.module_ident for m in gsdml.catalog()
                if m.size == size and m.direction == direction)


print("== multi-module offset uses module SIZE, not signal arrayElements ==")
# a module with a PARTIAL signal (1 of 4 bytes) must still push the next same-direction
# module to global byte 4 (the module size), not 1 (the signal's arrayElements).
_px, _pn = blob_pn.add_catalog_module((BLOB / "00_0module.xml").read_text("utf-8", "replace"),
                                      (NXD / "00_0module.nxd").read_bytes(),
                                      _catg(4, "input"), 1,
                                      signals=[dict(name="A", dtype="byte", byte=0, bit=0, arr=1)])
_px, _pn = blob_pn.add_catalog_module(_px, _pn, _catg(4, "input"), 2,
                                      signals=[dict(name="B", dtype="byte", byte=0, bit=0, arr=1)])
check("partial-signal module -> next module offset = size", device_aps(_px) == [(1, "0"), (2, "4")])

print("== rebuild_modules (capture -> rebuild reproduces the config) ==")
_rx, _rn = _blob_build([(1, _DA), (2, _DB), (3, _DC)])
_specs = blob_pn.capture_modules(_rx)
_rx2, _rn2 = blob_pn.rebuild_modules(_rx, _rn, _specs)
check("rebuild same order -> nxd byte-exact", _rn2 == _rn)
check("rebuild same order -> device-XML accessPath unchanged", device_aps(_rx2) == device_aps(_rx))

print("== device_lenprefix skips header '<' (only '<'+letter is the XML start) ==")
# a property-bag header u32 can equal the remaining length and sit right before a
# header "<\x00\x00\x00" (the '<' is the low byte of a u32) — earlier than the real XML.
# device_lenprefix must skip it (real start is "<" + a letter) so a rebuild chain with a
# shrinking/growing XML can't lock onto the wrong offset (the robot's drag crash).
import struct as _st
_xml = "<Module test>".encode("utf-16-le")
_real = _st.pack("<I", len(_xml)) + _xml
_spur = _st.pack("<I", 4 + len(_real)) + b"<\x00\x00\x00"   # spurious: u32==remaining, '<'+\0
_d = _spur + _real
check("device_lenprefix ignores header '<', finds real XML",
      blob_pn.device_lenprefix(_d) == len(_spur))
# self-heal: a wrong stored length (a file an older build corrupted) must still resolve
# to the XML start (first "<"+letter), so the next write fixes the prefix.
_hxml = '<Module systemTag="x">hi</Module>'.encode("utf-16-le")
_hdr = b"\x19\x00\x01\x00" + b"\x00" * 20            # header, no "<"+letter
_corrupt = _hdr + _st.pack("<I", 999999) + _hxml     # stored length is wrong
_lp = blob_pn.device_lenprefix(_corrupt)
check("device_lenprefix self-heals a corrupted length prefix",
      _corrupt[_lp + 4:] == _hxml)
# head-less config (built by 'New config'): deleting every module leaves header +
# u32(L) + whitespace only (no "<"). device_lenprefix must still find that prefix so
# the next add rebuilds from empty.
_ws = "\n".encode("utf-16-le") + b"\x00\x00"          # leftover "\n" + null
_empty = _hdr + _st.pack("<I", len(_ws)) + _ws
check("device_lenprefix finds the prefix of a whitespace-empty device XML",
      blob_pn.device_lenprefix(_empty) == len(_hdr)
      and _empty[len(_hdr) + 4:] == _ws)

print("== safety.generate_safe_variant (PROFINET non-safe -> safe by rename) ==")
# A Staeubli PROFINET safe config is byte-identical to the non-safe one bar the
# "_safe" name. generate_safe_variant must clone it preserving UUIDs/.nxd, retarget
# only the two name strings (loader + .spj ConfigXml) and make safe active.
import tempfile, shutil as _sh
from fbconfig import safety as _safety

def _make_robot(root):
    stem = "J207J208_PROFINET_NETX_51_RE_PNS_V3_5_35_-_V3_x"
    base = "J207J208"
    fb = root / "usr" / "fieldbus" / "hilscher"
    cfg = root / "usr" / "configs" / "hilscher"
    (fb / stem / "_S129").mkdir(parents=True)
    cfg.mkdir(parents=True)
    # loader .xml (BOM + the two name strings the rename must touch)
    loader = ('﻿<?xml version="1.0" encoding="utf-8"?>\r\n<SYCONnet>\r\n'
              '  <ProjectPath>.</ProjectPath>\r\n'
              f'  <ProjectFile>{stem}.spj</ProjectFile>\r\n'
              '  <Target UID="Item1" Protocol="PROFINET" ModuleName="NETX 51 RE/PNS '
              'V3.5.35 - V3.x" DtmProgID="Hilscher.PNIODevDTM2.1" '
              'PathToExportedFiles="..\\..\\configs\\hilscher" '
              f'BaseNameForExportedFiles="{base}" />\r\n</SYCONnet>\r\n')
    (fb / f"{stem}.xml").write_bytes(loader.encode("utf-8"))
    # .spj = CFB with ConfigXml (carries the SystemTag UUID that must be preserved)
    configxml = (f'<?xml version="1.0"?>\r\n<SYCONnet>\r\n\t<ProjectPath>.</ProjectPath>'
                 f'\r\n\t<ProjectFile>{stem}.spj</ProjectFile>\r\n\t<Target UID="Item1" '
                 f'Protocol="PROFINET" BaseNameForExportedFiles="{base}" '
                 'SystemTag="2b8f911a-da5e-4882-bddc-e3e32549ce3b"/>\r\n</SYCONnet>\r\n')
    (fb / f"{stem}.spj").write_bytes(
        cfb_write.build({"ConfigXml": configxml.encode("utf-8"), "Hardware": b"HW" * 20}))
    (fb / stem / "_S129" / "SYCON_net.xml").write_bytes(b"<SYCONnetProject/>")
    (cfg / f"{base}.nxd").write_bytes(b"NXD-PAYLOAD" * 32)
    (cfg / f"{base}_nwid.nxd").write_bytes(b"NWID" * 8)
    (cfg / f"{base}.xml").write_bytes(b'<ProcessData configMD5="ABC"/>')
    return stem, base, fb, cfg

_tmp = Path(tempfile.mkdtemp(prefix="fbce_safegen_"))
try:
    _stem, _base, _fb, _cfg = _make_robot(_tmp)
    _nxd_before = (_cfg / f"{_base}.nxd").read_bytes()
    _safety.generate_safe_variant(_tmp, backup=False)
    _ss = _safety.switch_state(_tmp)
    _safe_stem = _stem + "_safe"
    _stash = _cfg / "hilscher"          # STASH_REL = usr/configs/hilscher/hilscher
    # exports cloned byte-identical under _safe base, non-safe stashed
    check("generate: .nxd cloned byte-identical",
          (_cfg / "J207J208_safe.nxd").read_bytes() == _nxd_before)
    check("generate: _nwid.nxd cloned", (_cfg / "J207J208_safe_nwid.nxd").is_file())
    check("generate: non-safe exports stashed (not at active)",
          not (_cfg / "J207J208.nxd").exists() and (_stash / "J207J208.nxd").is_file())
    # loader retargeted, both name strings
    _ld = (_fb / f"{_safe_stem}.xml").read_bytes().decode("utf-8")
    check("generate: loader ProjectFile -> _safe", f"{_safe_stem}.spj" in _ld)
    check("generate: loader BaseName -> _safe",
          'BaseNameForExportedFiles="J207J208_safe"' in _ld)
    # .spj ConfigXml retargeted + UUID preserved + Hardware untouched
    with olefile.OleFileIO(str(_fb / f"{_safe_stem}.spj")) as _o:
        _tree = cfb_write.read_tree(_o)
    _cx = _tree["ConfigXml"].decode("utf-8")
    check("generate: .spj ConfigXml ProjectFile -> _safe", f"{_safe_stem}.spj" in _cx)
    check("generate: .spj ConfigXml BaseName -> _safe",
          'BaseNameForExportedFiles="J207J208_safe"' in _cx)
    check("generate: .spj UUID (SystemTag) PRESERVED",
          "2b8f911a-da5e-4882-bddc-e3e32549ce3b" in _cx)
    check("generate: .spj Hardware stream preserved", _tree["Hardware"] == b"HW" * 20)
    # SYCON_net.xml copied unchanged (UUIDs preserved)
    check("generate: SYCON_net.xml copied unchanged",
          (_fb / _safe_stem / "_S129" / "SYCON_net.xml").read_bytes() == b"<SYCONnetProject/>")
    # state: safe active, switchable back
    check("generate: switch_state active=safe, can_switch=True",
          _ss == {"active": "safe", "can_switch": True})
    # idempotency guard: running again must refuse (safe variant already present)
    _again_ok = False
    try:
        _safety.generate_safe_variant(_tmp, backup=False)
    except ValueError:
        _again_ok = True
    check("generate: refuses when safe variant already exists", _again_ok)
finally:
    _sh.rmtree(_tmp, ignore_errors=True)

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed.",
      "ALL GREEN" if not failed else f"FAILED: {failed}")
sys.exit(1 if failed else 0)
