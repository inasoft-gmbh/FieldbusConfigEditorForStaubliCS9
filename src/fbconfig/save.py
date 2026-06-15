"""Save orchestration: backup -> write all files -> round-trip self-check -> log.

The round-trip check re-reads what was just written and verifies it matches the
model (signal layout, sizes, node, .nxd MD5). If it fails, the change is reported
as UNVERIFIED; the timestamped backup created beforehand allows a clean restore.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from . import writers, project, backup, protocols


class SaveResult:
    def __init__(self):
        self.backup = None
        self.written = []
        self.verified = False
        self.problems = []


def _verify(model, paths) -> list[str]:
    """Re-read from disk and compare to the model. Returns list of problems."""
    problems = []
    reread = project.load(paths)
    for a, b in ((model.inp, reread.inp), (model.out, reread.out)):
        if a.max_bytes != b.max_bytes:
            problems.append(f"{a.direction}: max {a.max_bytes} != {b.max_bytes} on disk")
        if len(a.signals) != len(b.signals):
            problems.append(f"{a.direction}: {len(a.signals)} signals != {len(b.signals)}")
            continue
        for i, (sa, sb) in enumerate(zip(a.signals, b.signals)):
            if (sa.name, sa.sycon_dtype, sa.array_elements, sa.pad_before,
                sa.signal_type, sa.bit_offset) != \
               (sb.name, sb.sycon_dtype, sb.array_elements, sb.pad_before,
                sb.signal_type, sb.bit_offset):
                problems.append(f"{a.direction}[{i}] '{sa.name}' differs after reload")
            if sa.bit_offset is None and a.byte_offset(i) != b.byte_offset(i):
                problems.append(f"{a.direction}[{i}] offset differs after reload")
    if paths.nxd:
        info = reread.raw.get("nxd", {})
        if not info.get("md5_ok"):
            problems.append(".nxd MD5 invalid after write")
        # POWERLINK-specific .nxd field checks (EtherNet/IP has no such fields here)
        if "input_length" in info and info.get("input_length") != model.inp.max_bytes:
            problems.append(".nxd INPUT_LENGTH mismatch")
        if "node_id" in info and model.device.node_id is not None \
                and info.get("node_id") != model.device.node_id:
            problems.append(".nxd NODE_ID mismatch")
    return problems


def save(model, paths, when: datetime | None = None) -> SaveResult:
    when = when or datetime.now()
    res = SaveResult()

    # 1) backup first
    res.backup = backup.make_backup(paths, when)

    # 2) write the files via the protocol plugin (POWERLINK / EtherNet/IP)
    out = protocols.plugin_for(paths.protocol).write(model, paths)
    if out.get("sycon") is not None:
        Path(paths.sycon_xml).write_bytes(out["sycon"])
        res.written.append(paths.sycon_xml)
    if paths.val3_xml and out.get("val3") is not None:
        Path(paths.val3_xml).write_bytes(out["val3"])
        res.written.append(paths.val3_xml)
    if paths.nxd and out.get("nxd") is not None:
        Path(paths.nxd).write_bytes(out["nxd"])
        res.written.append(paths.nxd)
    if paths.nwid_nxd and out.get("nwid") is not None:
        Path(paths.nwid_nxd).write_bytes(out["nwid"])
        res.written.append(paths.nwid_nxd)

    # 3) round-trip self-check
    res.problems = _verify(model, paths)
    res.verified = not res.problems

    # 4) change log
    _log(paths, when, res)
    return res


def _log(paths, when, res: SaveResult):
    line = (f"{when:%Y-%m-%d %H:%M:%S}  "
            f"{'OK ' if res.verified else 'UNVERIFIED'}  "
            f"backup={Path(res.backup).name}  "
            f"files={[Path(p).name for p in res.written]}"
            + ("" if res.verified else f"  problems={res.problems}") + "\n")
    log = paths.robot_dir / "usr" / "fieldbus" / "_backups" / "changes.log"
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
