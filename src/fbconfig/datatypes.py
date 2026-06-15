"""Data type catalogue for fieldbus process data.

Authoritative list taken from the SyCon.net "Edit Signal" dialog (POWERLINK
CIFX RE/PLS). The catalogue key equals the dataType string used in the XML, so
the tool uses exactly the same names the user sees in SyCon.

A 'bit' signal usually occupies a full byte (8 bit flags, arrayElements=8). But
POWERLINK ALSO supports SINGLE sub-byte bits (arrayElements=1) at a specific
byte.bit position — e.g. accessPath "116.3" — exactly like EtherCAT/EtherNet-IP.
The writer tracks the bit index within a byte so these round-trip byte-exact (see
writers._iter_offsets). Sizes are in bytes (a single bit reports size 0).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DataType:
    key: str             # = dataType string in XML, e.g. 'bit','word','real32'
    size: int            # bytes consumed (per element; for 'bit' the byte it fills)
    array_elements: int  # default arrayElements (bit -> 8, else 1)


# Order = order in the SyCon dropdown (used for menus).
CATALOG = {
    "bit":        DataType("bit",        1, 8),
    "byte":       DataType("byte",       1, 1),
    "signed8":    DataType("signed8",    1, 1),
    "unsigned8":  DataType("unsigned8",  1, 1),
    "word":       DataType("word",       2, 1),
    "signed16":   DataType("signed16",   2, 1),
    "unsigned16": DataType("unsigned16", 2, 1),
    "dword":      DataType("dword",      4, 1),
    "signed32":   DataType("signed32",   4, 1),
    "unsigned32": DataType("unsigned32", 4, 1),
    "real32":     DataType("real32",     4, 1),
}


def by_sycon(sycon_dtype: str) -> DataType:
    """Map an XML dataType string to a DataType (1-byte fallback if unknown)."""
    return CATALOG.get(sycon_dtype, DataType(sycon_dtype, 1, 1))


def size_of(sycon_dtype: str) -> int:
    return by_sycon(sycon_dtype).size


def bit_width(sycon_dtype: str, array_elements: int) -> int:
    """Width in BITS. Works for both granularities of the 'bit' type:
    POWERLINK packs 8 flags into one signal (arrayElements=8 -> 8 bits = 1 byte),
    EtherNet/IP uses one signal per bit (arrayElements=1 -> 1 bit). All other
    types are whole bytes: size * 8 * arrayElements."""
    dt = by_sycon(sycon_dtype)
    if dt.key == "bit":
        return array_elements
    return dt.size * 8 * array_elements
