"""POWERLINK plugin — wraps the original, byte-validated read/write path.

This is the reference implementation (CIFX RE/PLS). Two modules ("N Bytes In",
"N Bytes Out") with moduleAddress 1/2; bit signals pack 8 flags (arrayElements=8);
INPUT_LENGTH/OUTPUT_LENGTH params; .nxd lengths @324/@326.
"""
from __future__ import annotations
from pathlib import Path

from .. import sycon, nxd, writers


def load(paths):
    model = sycon.load(paths.sycon_xml)
    model.device.base_name = paths.base_name
    model.device.protocol = model.device.protocol or "POWERLINK"
    model.raw["paths"] = paths
    model.raw["protocol_kind"] = "powerlink"
    if paths.nxd:
        info = nxd.read(paths.nxd)
        model.device.node_id = info["node_id"]
        # Blob is master for the network name (param DNS_NODE_NAME, set by sycon.load).
        # Only fall back to the .nxd copy for legacy files whose blob name is empty.
        if not model.device.node_name:
            model.device.node_name = info["name"]
        model.device.ip = info["ip"]
        model.raw["nxd"] = info
    return model


def write(model, paths) -> dict:
    out = {"sycon": writers.write_sycon(model, paths.sycon_xml), "val3": None, "nxd": None}
    if paths.val3_xml:
        out["val3"] = writers.write_val3(model, paths.val3_xml)
    if paths.nxd:
        out["nxd"] = writers.write_nxd(model, paths.nxd)
    return out
