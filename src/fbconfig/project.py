"""Discover and load a robot's fieldbus configuration from its main folder.

Layout under <robot>/usr/:
  fieldbus/<name>/<Base>.spj  + <Base>/_S129/SYCON_net.xml   (DTM project)
  configs/<name>/<Base>.nxd   + <Base>.xml                    (exports)
"""
from __future__ import annotations
import re
import os
import shutil
from pathlib import Path
from dataclasses import dataclass

from . import sycon, nxd, protocols
from .model import ConfigModel


@dataclass
class ProjectPaths:
    robot_dir: Path
    spj: Path
    sycon_xml: Path
    nxd: Path | None
    val3_xml: Path | None
    export_dir: Path
    base_name: str
    protocol: str = ""          # from the loader's Protocol="..." attribute
    nwid_nxd: Path | None = None  # <base>_nwid.nxd network-id image (PROFINET/EIP/safe)


def discover(robot_dir) -> list[ProjectPaths]:
    """Find all fieldbus projects under a robot main folder."""
    robot = Path(robot_dir)
    found: list[ProjectPaths] = []
    fieldbus = robot / "usr" / "fieldbus"
    if not fieldbus.is_dir():
        return found
    for spj in fieldbus.glob("*/*.spj"):
        sycon_xml = spj.parent / spj.stem / "_S129" / "SYCON_net.xml"
        if not sycon_xml.is_file():
            continue
        # The loader .xml (same stem) holds the export base name + path + protocol.
        export_base, export_dir, protocol = _export_target(spj, robot)
        nxd_p = export_dir / f"{export_base}.nxd"
        xml_p = export_dir / f"{export_base}.xml"
        nwid_p = export_dir / f"{export_base}_nwid.nxd"   # network identity (IP/name)
        found.append(ProjectPaths(
            robot_dir=robot, spj=spj, sycon_xml=sycon_xml,
            nxd=nxd_p if nxd_p.is_file() else None,
            val3_xml=xml_p if xml_p.is_file() else None,
            export_dir=export_dir, base_name=export_base, protocol=protocol,
            nwid_nxd=nwid_p if nwid_p.is_file() else None,
        ))
    return found


def _export_target(spj: Path, robot: Path):
    """Read BaseNameForExportedFiles + PathToExportedFiles + Protocol from loader."""
    base = spj.stem
    path_rel = None
    protocol = ""
    loader = spj.with_suffix(".xml")
    if loader.is_file():
        t = loader.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'BaseNameForExportedFiles="([^"]*)"', t)
        if m:
            base = m.group(1)
        m = re.search(r'PathToExportedFiles="([^"]*)"', t)
        if m:
            path_rel = m.group(1)
        m = re.search(r'Protocol="([^"]*)"', t)
        if m:
            protocol = m.group(1)
    if path_rel:
        export_dir = Path(os.path.normpath(spj.parent / path_rel.replace("\\", os.sep)))
    else:
        export_dir = robot / "usr" / "configs" / spj.parent.name
    return base, export_dir, protocol


def load(paths: ProjectPaths) -> ConfigModel:
    """Dispatch to the protocol plugin selected by the loader's Protocol attr."""
    from . import safety
    model = protocols.plugin_for(paths.protocol).load(paths)
    model.raw["safety"] = safety.detect(paths)
    return model


def add_config(target_robot_dir, source_robot_dir) -> list[ProjectPaths]:
    """Create a fieldbus configuration in a robot that has none, by cloning an
    existing one from `source_robot_dir` (a valid SyCon project can't be built
    from scratch). Copies the fieldbus project(s), the exports, and any safety
    files into the target. The user then edits names / IP / signals. Returns the
    discovered ProjectPaths in the target. Refuses to overwrite an existing
    fieldbus config."""
    src, tgt = Path(source_robot_dir), Path(target_robot_dir)
    src_fb = src / "usr" / "fieldbus" / "hilscher"
    if not src_fb.is_dir() or not discover(src):
        raise ValueError("The chosen template folder has no fieldbus configuration.")
    if discover(tgt):
        raise ValueError("This robot already has a fieldbus configuration.")

    def clone_dir(s: Path, d: Path):
        d.mkdir(parents=True, exist_ok=True)
        for item in s.iterdir():
            if item.name == "_backups":
                continue
            dst = d / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)

    clone_dir(src_fb, tgt / "usr" / "fieldbus" / "hilscher")
    src_cfg = src / "usr" / "configs" / "hilscher"
    if src_cfg.is_dir():
        clone_dir(src_cfg, tgt / "usr" / "configs" / "hilscher")
    for rel in ("usr/configs/safety.pmt2", "usr/configs/safetyStruct.json"):
        s = src / rel
        if s.is_file():
            (tgt / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, tgt / rel)
    return discover(tgt)


def add_config_from_template(target_robot_dir, template_zip) -> list[ProjectPaths]:
    """Create a fieldbus configuration by cloning a bundled per-protocol template
    ZIP (a minimal reference robot config). The user then edits names / IP /
    signals. Returns the discovered ProjectPaths in the target."""
    import tempfile
    import zipfile
    tmp = tempfile.mkdtemp(prefix="fbtmpl_")
    try:
        with zipfile.ZipFile(template_zip) as z:
            z.extractall(tmp)
        return add_config(target_robot_dir, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
