"""Functional-safety (FSoE for EtherCAT, PROFIsafe for PROFINET): detect + switch.

A robot's fieldbus comes in a SAFE and a NON-SAFE variant — separate, robot-
specific SyCon projects (the safe one carries the FSoE/PROFIsafe PDOs and robot-
side safety config files). Observed, consistent layout (several validated robots):

  fieldbus/hilscher/<name>.spj AND <name>_safe.spj  both coexist (untouched)
  configs/hilscher/<base>.{nxd,xml,json,...}        ACTIVE export set (top level)
  configs/hilscher/hilscher/<other>.{nxd,xml,...}   INACTIVE export set (stashed)
  configs/safety.pmt2, configs/safetyStruct.json    present when SAFE is active

For PROFINET the safe variant IS generated here (generate_safe_variant) — it is
byte-identical to the non-safe one bar the "_safe" name. For EtherCAT/FSoE the
safe PDOs genuinely differ, so the SWITCH just toggles which export set is active
and moves the safety files (never deletes; ZIP backup first); it needs BOTH
variants to exist (the target variant's exports stashed) — prepared in SyCon.
"""
from __future__ import annotations
import shutil
from pathlib import Path

SAFETY_FILES = ["usr/configs/safety.pmt2", "usr/configs/safetyStruct.json"]
SAFETY_DIRS = ["usr/configs/safety", "usr/templates/safeCalibration"]

ACTIVE_REL = "usr/configs/hilscher"
STASH_REL = "usr/configs/hilscher/hilscher"
SAFETY_STASH_REL = "usr/configs/hilscher/_safety_stash"
NONSAFE_BASE = "J207J208"
SAFE_BASE = "J207J208_safe"


def is_safe_project(paths) -> bool:
    """True if this fieldbus project is the safe variant (FSoE / PROFIsafe)."""
    return "_safe" in Path(paths.spj).stem.lower() or \
           str(paths.base_name).lower().endswith("_safe")


def present(robot_dir) -> list[str]:
    """Robot-side safety files/dirs that exist under the robot folder."""
    root = Path(robot_dir)
    out = [rel for rel in SAFETY_FILES if (root / rel).is_file()]
    out += [rel + "/" for rel in SAFETY_DIRS if (root / rel).is_dir()]
    return out


def detect(paths) -> dict:
    """Safety summary for a loaded project."""
    proto = (paths.protocol or "").lower()
    tech = ("FSoE" if "ethercat" in proto or "ecs" in proto
            else "PROFIsafe" if "profinet" in proto or "pns" in proto else "Safety")
    st = switch_state(paths.robot_dir)
    return {"safe": is_safe_project(paths), "tech": tech,
            "files": present(paths.robot_dir),
            "active": st["active"], "can_switch": st["can_switch"]}


# ----------------------------------------------------------- variant switching
def _exports(folder: Path, base: str) -> list[Path]:
    """Files in `folder` belonging to export `base`: base.nxd/.xml/.json/.xml.bak
    AND base_nwid.nxd (PROFINET network-id image). The stem before the first '.'
    must be `base` or `base_nwid`, so NONSAFE_BASE never picks up SAFE_BASE files."""
    if not folder.is_dir():
        return []
    return [f for f in folder.iterdir()
            if f.is_file() and f.name.split(".")[0] in (base, base + "_nwid")]


def switch_state(robot_dir) -> dict:
    """Which variant is active and whether the other can be switched in."""
    root = Path(robot_dir)
    active, stash = root / ACTIVE_REL, root / STASH_REL
    safe_top = bool(_exports(active, SAFE_BASE))
    nonsafe_top = bool(_exports(active, NONSAFE_BASE))
    cur_safe = safe_top and not nonsafe_top
    can = bool(_exports(stash, NONSAFE_BASE) if cur_safe
               else _exports(stash, SAFE_BASE))
    return {"active": "safe" if cur_safe else "nonsafe", "can_switch": can}


def _move(src: Path, dst_dir: Path):
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
    src.replace(dst)


# safety artifacts toggled with the variant: the two config files + the
# usr/configs/safety/ folder (NOT templates/safeCalibration, a permanent template)
SAFETY_TOGGLE = [Path(r).name for r in SAFETY_FILES] + ["safety"]


def _toggle_safety(cfg: Path, stash: Path, to_safe: bool) -> list[str]:
    moved = []
    for nm in SAFETY_TOGGLE:
        src = (stash if to_safe else cfg) / nm
        if src.exists():
            _move(src, cfg if to_safe else stash)
            moved.append(nm)
    return moved


def switch(robot_dir, backup: bool = True) -> dict:
    """Toggle safe <-> non-safe by moving export sets + safety files (never
    deletes; ZIP backup of usr/configs first). Returns {new_active, moved,
    backup}. Raises ValueError if the target variant isn't available."""
    import zipfile
    root = Path(robot_dir)
    st = switch_state(root)
    if not st["can_switch"]:
        raise ValueError(
            "Cannot switch: the other variant's exports are not stashed. Both a "
            "safe and a non-safe configuration must exist (prepared in SyCon).")
    to_safe = st["active"] == "nonsafe"
    active, stash = root / ACTIVE_REL, root / STASH_REL
    safety_stash = root / SAFETY_STASH_REL
    cfg = root / "usr" / "configs"
    cur_base = SAFE_BASE if st["active"] == "safe" else NONSAFE_BASE
    tgt_base = SAFE_BASE if to_safe else NONSAFE_BASE

    backup_path = None
    if backup:
        bdir = root / "usr" / "fieldbus" / "_backups"
        bdir.mkdir(parents=True, exist_ok=True)
        backup_path = bdir / "safety_switch_backup.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in cfg.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(root))

    moved = []
    for f in _exports(active, cur_base):          # active -> stash
        _move(f, stash)
        moved.append(f"stash {f.name}")
    for f in _exports(stash, tgt_base):           # stash -> active
        _move(f, active)
        moved.append(f"activate {f.name}")
    moved += _toggle_safety(cfg, safety_stash, to_safe)   # safety files + folder

    return {"new_active": "safe" if to_safe else "nonsafe", "moved": moved,
            "backup": str(backup_path) if backup_path else None}


# ----------------------------------------------- generate safe variant (PROFINET)
# A Staeubli CS9 PROFINET config "with safety profile" is, at the fieldbus level,
# BYTE-IDENTICAL to the same config without it (verified by diffing two SRS-created
# projects: the exported .nxd / _nwid.nxd are byte-equal; only the project NAME gets
# the "_safe" suffix + freshly randomised GUIDs differ). SRS marks a config "(Safe)"
# in the Transfer Manager purely from that "_safe" name. The actual PROFIsafe safety
# lives in the Val3 safety program (loaded separately via SRS) and in the GSDML for
# the PLC -- never in the netX fieldbus config (black channel). So we can turn an
# existing non-safe PROFINET config into the safe variant by a pure rename that
# PRESERVES every UUID, the configMD5 and the .nxd (no regeneration).

def _replace_once(data: bytes, old: bytes, new: bytes, where: str) -> bytes:
    """Byte-replace exactly one occurrence; raise if `old` isn't found (no silent
    no-op -- byte exactness matters)."""
    if data.count(old) != 1:
        raise ValueError(f"{where}: expected exactly one '{old.decode(errors='replace')}'"
                         f", found {data.count(old)}")
    return data.replace(old, new, 1)


def _name_to_safe(stem: str, base: str, safe_stem: str, safe_base: str):
    """(old, new) byte pairs that rename a project's references to the _safe variant:
    the .spj ProjectFile name and BaseNameForExportedFiles. Used for both the loader
    .xml and the .spj's internal ConfigXml stream (identical strings)."""
    return [
        (f"{stem}.spj".encode("utf-8"), f"{safe_stem}.spj".encode("utf-8")),
        (f'BaseNameForExportedFiles="{base}"'.encode("utf-8"),
         f'BaseNameForExportedFiles="{safe_base}"'.encode("utf-8")),
    ]


def _patch_loader_xml(path: Path, stem, base, safe_stem, safe_base):
    data = path.read_bytes()
    for old, new in _name_to_safe(stem, base, safe_stem, safe_base):
        data = _replace_once(data, old, new, f"loader {path.name}")
    path.write_bytes(data)


def _patch_spj_configxml(path: Path, stem, base, safe_stem, safe_base):
    """Rewrite the .spj (a CFB compound file) so its ConfigXml stream points at the
    _safe name. Everything else (incl. the project SystemTag UUID) is preserved."""
    import olefile
    from . import cfb_write
    with olefile.OleFileIO(str(path)) as ole:
        tree = cfb_write.read_tree(ole)
    cx = tree.get("ConfigXml")
    if not isinstance(cx, (bytes, bytearray)):
        raise ValueError(f"{path.name}: ConfigXml stream not found")
    cx = bytes(cx)
    for old, new in _name_to_safe(stem, base, safe_stem, safe_base):
        cx = _replace_once(cx, old, new, f"{path.name}/ConfigXml")
    tree["ConfigXml"] = cx
    path.write_bytes(cfb_write.build(tree))


def can_generate_safe(paths) -> bool:
    """True if `generate_safe_variant` applies: an ACTIVE, non-safe PROFINET project
    with no _safe counterpart yet."""
    proto = (paths.protocol or "").lower()
    if "profinet" not in proto and "pns" not in proto:
        return False
    if is_safe_project(paths):
        return False
    safe_stem = Path(paths.spj).stem + "_safe"
    return not (Path(paths.spj).parent / f"{safe_stem}.spj").exists()


def generate_safe_variant(robot_dir, backup: bool = True) -> dict:
    """Create the PROFIsafe ('_safe') variant from the EXISTING non-safe PROFINET
    config by pure rename -- UUIDs, configMD5 and the exported .nxd are preserved
    byte-for-byte. Makes the safe variant active and stashes the non-safe one (so a
    normal `switch()` toggles back). Does NOT create safety.pmt2/safetyStruct.json --
    the safety program is loaded separately via SRS. Raises ValueError if not
    applicable (wrong protocol / already safe / safe variant already present)."""
    root = Path(robot_dir)
    p = _active_project(root)
    if not p:
        raise ValueError("No active fieldbus configuration found.")
    proto = (p.protocol or "").lower()
    if "profinet" not in proto and "pns" not in proto:
        raise ValueError(
            "Generating a safe variant by renaming is only valid for PROFINET "
            "(its safe config is byte-identical to the non-safe one bar the _safe "
            "name). For other protocols use a saved template.")
    if is_safe_project(p):
        raise ValueError("This configuration is already the safe variant.")

    base = p.base_name                       # e.g. J207J208
    safe_base = base + "_safe"
    spj = Path(p.spj)
    stem = spj.stem                          # J207J208_PROFINET_..._V3_x
    safe_stem = stem + "_safe"
    fb = spj.parent                          # usr/fieldbus/hilscher
    active, stash = root / ACTIVE_REL, root / STASH_REL
    if (fb / f"{safe_stem}.spj").exists():
        raise ValueError(f"A safe variant ({safe_stem}.spj) already exists.")
    if not _exports(active, base):
        raise ValueError("The non-safe export set is not present at the active "
                         "location -- nothing to clone.")

    backup_path = None
    if backup:
        import zipfile
        cfg = root / "usr" / "configs"
        bdir = root / "usr" / "fieldbus" / "_backups"
        bdir.mkdir(parents=True, exist_ok=True)
        backup_path = bdir / "generate_safe_backup.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in cfg.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(root))

    # 1) clone the fieldbus project: folder (SYCON_net.xml = UUIDs untouched), the
    #    .spj and the loader .xml, then retarget the two name strings to _safe.
    shutil.copytree(fb / stem, fb / safe_stem)
    shutil.copy2(spj, fb / f"{safe_stem}.spj")
    _patch_spj_configxml(fb / f"{safe_stem}.spj", stem, base, safe_stem, safe_base)
    loader = spj.with_suffix(".xml")
    if loader.is_file():
        shutil.copy2(loader, fb / f"{safe_stem}.xml")
        _patch_loader_xml(fb / f"{safe_stem}.xml", stem, base, safe_stem, safe_base)

    # 2) clone the exports under the _safe base (content byte-identical).
    cloned = []
    for f in _exports(active, base):
        newname = f.name.replace(base, safe_base, 1)
        shutil.copy2(f, active / newname)
        cloned.append(newname)

    # 3) make safe active by stashing the non-safe exports (switch() can toggle back).
    for f in _exports(active, base):
        _move(f, stash)

    return {"new_active": "safe", "base": safe_base, "stem": safe_stem,
            "cloned": cloned, "backup": str(backup_path) if backup_path else None}


# --------------------------------------------------------- variant templates
# A robot may ship with only ONE variant. If the process image is standardised,
# the OTHER variant can be reused from a saved template (the robot-specific IP /
# station is then set via General). Templates live next to settings.json.
import json
from .paths import appdata_dir, asset

TEMPLATE_DIR = appdata_dir() / "templates" / "fieldbus"   # user-saved templates
BUNDLED_DIR = Path(asset("templates/safety"))             # shipped with the app


def _template_roots():
    """Folders that hold variant templates — user-saved first, then the bundled ones."""
    return [d for d in (TEMPLATE_DIR, BUNDLED_DIR) if d.is_dir()]


def _template_dir(name: str) -> Path:
    """Resolve a template by name (a user-saved one shadows a bundled one)."""
    for root in _template_roots():
        if (root / name / "meta.json").is_file():
            return root / name
    return TEMPLATE_DIR / name


def _active_project(robot_dir):
    from . import project as _proj
    cand = [p for p in _proj.discover(robot_dir) if p.nxd or p.val3_xml]
    return cand[0] if cand else None


def save_template(robot_dir, name: str) -> str:
    """Save the robot's ACTIVE variant (fieldbus project + exports + safety files)
    as a reusable template. Returns the template path."""
    from . import project as _proj
    root = Path(robot_dir)
    p = _active_project(root)
    if not p:
        raise ValueError("No active fieldbus configuration to save.")
    safe = is_safe_project(p)
    tdir = TEMPLATE_DIR / name
    if tdir.exists():
        shutil.rmtree(tdir)
    (tdir / "fieldbus").mkdir(parents=True)
    (tdir / "configs").mkdir()
    # fieldbus project: the .spj, its loader .xml and the project folder
    spj = Path(p.spj)
    for f in (spj, spj.with_suffix(".xml")):
        if f.is_file():
            shutil.copy2(f, tdir / "fieldbus" / f.name)
    shutil.copytree(spj.parent / spj.stem, tdir / "fieldbus" / spj.stem)
    # active exports of this base
    cfg = root / ACTIVE_REL
    for f in _exports(cfg, p.base_name):
        shutil.copy2(f, tdir / "configs" / f.name)
    # safety files
    if safe:
        (tdir / "safety").mkdir()
        for rel in SAFETY_FILES:
            s = root / rel
            if s.is_file():
                shutil.copy2(s, tdir / "safety" / Path(rel).name)
        sd = root / "usr" / "configs" / "safety"
        if sd.is_dir():
            shutil.copytree(sd, tdir / "safety" / "safety")
    meta = {"kind": "safe" if safe else "nonsafe", "protocol": p.protocol,
            "base": p.base_name, "spj": spj.name}
    (tdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return str(tdir)


def list_templates() -> list[dict]:
    """Variant templates [{name, kind, protocol, base, …}] — user-saved + bundled.
    A user-saved template shadows a bundled one of the same name."""
    out, seen = [], set()
    for root in _template_roots():
        for d in sorted(root.iterdir()):
            mf = d / "meta.json"
            if d.name not in seen and mf.is_file():
                seen.add(d.name)
                out.append({"name": d.name, **json.loads(mf.read_text(encoding="utf-8"))})
    return out


def apply_template(robot_dir, name: str, backup: bool = True) -> dict:
    """Install a saved variant template into the robot (making it the ACTIVE
    variant), stashing whatever variant is currently active. Used to switch FSoE
    on/off when the target variant is not already present."""
    import zipfile
    root = Path(robot_dir)
    tdir = _template_dir(name)                    # user-saved or bundled
    meta = json.loads((tdir / "meta.json").read_text(encoding="utf-8"))
    to_safe = meta["kind"] == "safe"
    active, stash = root / ACTIVE_REL, root / STASH_REL
    safety_stash = root / SAFETY_STASH_REL
    cfg = root / "usr" / "configs"
    active.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if backup and active.exists():
        bdir = root / "usr" / "fieldbus" / "_backups"
        bdir.mkdir(parents=True, exist_ok=True)
        backup_path = bdir / "safety_template_backup.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in cfg.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(root))

    # stash the current active exports + safety files (opposite kind)
    cur = _active_project(root)
    if cur:
        for f in _exports(active, cur.base_name):
            _move(f, stash)
        if is_safe_project(cur):                 # currently safe -> stash safety
            _toggle_safety(cfg, safety_stash, to_safe=False)

    # install template fieldbus project + exports (+ safety files/folder)
    fb = root / "usr" / "fieldbus" / "hilscher"
    fb.mkdir(parents=True, exist_ok=True)
    for item in (tdir / "fieldbus").iterdir():
        dst = fb / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        elif not dst.exists():
            shutil.copy2(item, dst)
    active.mkdir(parents=True, exist_ok=True)
    for f in (tdir / "configs").iterdir():
        shutil.copy2(f, active / f.name)
    if to_safe and (tdir / "safety").is_dir():
        for item in (tdir / "safety").iterdir():
            dst = cfg / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
    return {"new_active": meta["kind"], "backup": str(backup_path) if backup_path else None}
