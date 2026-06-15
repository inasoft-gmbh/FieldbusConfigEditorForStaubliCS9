"""Read the compiled netX image J207J208.nxd.

Format (docs/06_NXD_FORMAT.md): header [0:136], data [136:end];
MD5 of data @0x54; INPUT_LENGTH u16 @324, OUTPUT_LENGTH u16 @326;
NODE_ID byte @364; DNS name @328; IP (LE) @360. Only @324/@326 + MD5 change
with size. Write-back: prototype/gen_nxd.py.
"""
from __future__ import annotations
import struct
import hashlib

OFF_MD5, DATA_START = 0x54, 136
OFF_IN, OFF_OUT, OFF_NODE, OFF_NAME, OFF_IP = 324, 326, 364, 328, 360


def read(path) -> dict:
    d = open(path, "rb").read()
    md5_ok = hashlib.md5(d[DATA_START:]).digest() == d[OFF_MD5:OFF_MD5 + 16]
    return {
        "size": len(d),
        "md5_ok": md5_ok,
        "input_length": struct.unpack_from("<H", d, OFF_IN)[0],
        "output_length": struct.unpack_from("<H", d, OFF_OUT)[0],
        "node_id": d[OFF_NODE],
        "name": d[OFF_NAME:OFF_NAME + 16].split(b"\x00")[0].decode("latin1"),
        "ip": f"{d[OFF_IP+3]}.{d[OFF_IP+2]}.{d[OFF_IP+1]}.{d[OFF_IP]}",
    }
