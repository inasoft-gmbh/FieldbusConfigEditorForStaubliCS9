"""EtherCAT plugin (Hilscher CPT 9.x RE/ECS). Same SyCon framework + signal
format as EtherNet/IP; the detail has two flat modules: RxPDO (signalType
'input' -> In) and TxPDO (signalType 'output' -> Out). Byte-packed bit signals
(arrayElements=8) with UPPER-CASE GUIDs. Read + byte-exact identity/rename write
are validated against many real projects; structural editing is not enabled
(per-PDO offset semantics differ), but the safety (FSoE) toggle works at the
file level (see protocols.safety).
"""
from __future__ import annotations
from . import ethernetip as _eip


def load(paths):
    return _eip.generic_load(paths, "EtherCAT")


def write(model, paths) -> dict:
    return _eip.generic_write(model, paths)
