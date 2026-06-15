"""Diagnostics for PROFINET blob/nxd operations.

When a module/signal operation fails (e.g. the device-XML length prefix can't be
found), we want to know WHERE the failure came from (full traceback) and WHAT the
blob looked like (which file, how many <BinData>, which CFB streams, the device-XML
stream size + first bytes). `log_failure` writes all of that to a log file next to
the robot so the user can send it back."""
from __future__ import annotations

import io
import re
import traceback
from datetime import datetime


def blob_report(xml_text: str) -> list[str]:
    """Human-readable lines describing the device blob inside a SyCon project XML."""
    import olefile
    from . import sycon, blob_pn
    out = []
    try:
        out.append(f"<BinData> count: {len(re.findall(r'<BinData', xml_text))}")
        raw = sycon.blob_from_xml(xml_text)
        cfb = raw[4:]
        out.append(f"blob bytes (after u32 len): {len(cfb)}")
        ole = olefile.OleFileIO(io.BytesIO(cfb))
        streams = ["/".join(p) for p in ole.listdir()]
        out.append(f"CFB streams: {len(streams)}")
        dev_paths = [p for p in ole.listdir() if p[-1] == "PNIODeviceDataModelBasic"]
        if dev_paths:
            dev = ole.openstream(dev_paths[0]).read()
            needle = "<Module".encode("utf-16-le")
            out.append(f"PNIODeviceDataModelBasic: {len(dev)} B, "
                       f"first16={dev[:16].hex()}, '<Module'@={dev.find(needle)}")
            try:
                lp = blob_pn.device_lenprefix(dev)
                out.append(f"  device_lenprefix OK -> lp={lp}")
            except Exception as e:
                out.append(f"  device_lenprefix FAILS -> {e}")
        else:
            out.append("PNIODeviceDataModelBasic stream: MISSING. streams="
                       + ", ".join(streams[:30]))
        ole.close()
    except Exception as e:
        out.append(f"(blob_report failed: {e!r})")
    return out


def log_failure(robot_dir, paths, title, exc, now=None) -> str:
    """Append a full diagnostic record (traceback + blob report) to a log file in the
    robot folder and return its path."""
    import os
    now = now or datetime.now()
    lines = [f"===== {title}  @ {now.isoformat(timespec='seconds')} =====",
             f"error: {type(exc).__name__}: {exc}"]
    sx = getattr(paths, "sycon_xml", None) if paths else None
    nx = getattr(paths, "nxd", None) if paths else None
    lines.append(f"sycon_xml: {sx}")
    lines.append(f"nxd: {nx} (exists={nx.is_file() if nx else None})")
    if sx is not None:
        try:
            lines += blob_report(sx.read_text("utf-8", "replace"))
        except Exception as e:
            lines.append(f"(could not read sycon_xml: {e!r})")
    lines.append("traceback:")
    lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    log_path = os.path.join(str(robot_dir) if robot_dir else ".", "fbce_pn_debug.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    except Exception:
        log_path = "(could not write log)"
    return log_path
