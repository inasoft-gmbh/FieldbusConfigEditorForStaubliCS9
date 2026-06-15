"""Timestamped ZIP backup of a robot's fieldbus configuration before any change."""
from __future__ import annotations
import zipfile
from pathlib import Path
from datetime import datetime


def make_backup(paths, when: datetime | None = None) -> Path:
    """Zip the fieldbus project folder + the export folder into a dated archive.
    Returns the archive path. Stored under <robot>/usr/fieldbus/_backups/.
    """
    when = when or datetime.now()
    stamp = when.strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = paths.robot_dir / "usr" / "fieldbus" / "_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    archive = backup_dir / f"{paths.base_name}_{stamp}.zip"

    project_folder = paths.spj.parent                 # usr/fieldbus/<name>
    targets = [paths.spj, spj_loader(paths.spj),
               *project_folder.glob(f"{paths.spj.stem}/**/*")]
    if paths.nxd:
        targets.append(paths.nxd)
    if paths.val3_xml:
        targets.append(paths.val3_xml)

    root = paths.robot_dir
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for t in targets:
            t = Path(t)
            if t.is_file():
                z.write(t, t.relative_to(root))
    return archive


def spj_loader(spj: Path) -> Path:
    return spj.with_suffix(".xml")
