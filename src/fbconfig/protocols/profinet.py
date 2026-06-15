"""PROFINET plugin (Hilscher netX 51 RE/PNS). Same SyCon framework + signal
format as EtherNet/IP; the detail has nested Slot/Subslot modules ("8 Bytes
Input", "16 Bytes Output", ...). In = signalType 'input', Out = 'output'.
signalAccessPath is per-slot, so offsets are preserved verbatim (identity/rename
are byte-exact); structural editing is not enabled. The safety (PROFIsafe)
toggle works at the file level (see protocols.safety).
"""
from __future__ import annotations
from . import ethernetip as _eip


def load(paths):
    return _eip.generic_load(paths, "PROFINET")


def write(model, paths) -> dict:
    return _eip.generic_write(model, paths)
