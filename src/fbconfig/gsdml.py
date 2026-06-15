"""PROFINET module catalog, read from the bundled GSDML device description.

The GSDML (`assets/gsdml/GSDML-*.xml`) defines every pluggable module with its
PROFINET ModuleIdentNumber / SubmoduleIdentNumber and its input/output data size —
exactly what a slot must carry so the device matches the PLC. Drop a newer GSDML
into assets/gsdml/ to update the catalog (the newest by filename wins).
"""
from __future__ import annotations
import glob
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .paths import asset, appdata_dir

_DTYPE_LEN = {"Unsigned8": 1, "Integer8": 1, "Unsigned16": 2, "Integer16": 2,
              "Unsigned32": 4, "Integer32": 4, "Float32": 4}


@dataclass(frozen=True)
class Module:
    name: str                 # e.g. "1 Byte Input"
    module_ident: int         # ModuleIdentNumber (must match the PLC's GSDML config)
    submodule_ident: int      # SubmoduleIdentNumber
    in_bytes: int             # device input  = data the device PRODUCES (PLC "Eingang")
    out_bytes: int            # device output = data the device CONSUMES (PLC "Ausgang")

    @property
    def direction(self) -> str:
        return "input" if self.in_bytes else "output"

    @property
    def size(self) -> int:
        return self.in_bytes or self.out_bytes


def gsdml_path() -> str | None:
    """The GSDML to use: a user-supplied one next to the program/project (in a
    `gsdml/` folder) wins so it can be replaced without a rebuild; otherwise the
    bundled one. Newest by filename within each location."""
    for folder in (os.path.join(str(appdata_dir()), "gsdml"), asset("gsdml")):
        files = sorted(glob.glob(os.path.join(folder, "GSDML-*.xml")))
        if files:
            return files[-1]
    return None


def _texts(root, ns) -> dict:
    out = {}
    for ti in root.iter(f"{{{ns}}}Text"):
        out[ti.get("TextId")] = ti.get("Value")
    return out


def _datalen(io_dir, ns) -> int:
    n = 0
    for di in io_dir.iter(f"{{{ns}}}DataItem"):
        L = _DTYPE_LEN.get(di.get("DataType", ""))
        if L is None and di.get("Length"):
            L = int(di.get("Length"))
        n += L or 0
    return n


_cache: list[Module] | None = None


def catalog() -> list[Module]:
    """All catalog modules (cached). Empty if no GSDML is bundled."""
    global _cache
    if _cache is not None:
        return _cache
    path = gsdml_path()
    if not path:
        _cache = []
        return _cache
    root = ET.parse(path).getroot()
    ns = root.tag.split("}")[0].strip("{")
    texts = _texts(root, ns)
    mods: list[Module] = []
    for mi in root.iter(f"{{{ns}}}ModuleItem"):
        mident = mi.get("ModuleIdentNumber")
        name = mident
        info = mi.find(f"{{{ns}}}ModuleInfo")
        if info is not None:
            nm = info.find(f"{{{ns}}}Name")
            if nm is not None:
                name = texts.get(nm.get("TextId"), nm.get("TextId"))
        for sm in mi.iter(f"{{{ns}}}VirtualSubmoduleItem"):
            sident = sm.get("SubmoduleIdentNumber")
            io = sm.find(f"{{{ns}}}IOData")
            inl = outl = 0
            if io is not None:
                for ch in io:
                    tag = ch.tag.split("}")[-1]
                    if tag == "Input":
                        inl = _datalen(ch, ns)
                    elif tag == "Output":
                        outl = _datalen(ch, ns)
            if (inl or outl) and mident and sident:
                mods.append(Module(name, int(mident, 0), int(sident, 0), inl, outl))
    _cache = mods
    return mods
