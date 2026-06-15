"""Guided editing menu. Modifies the in-memory ConfigModel via the core API.
All byte-accounting rules live in the model; this layer only collects input.
Saving to disk (backup ZIP + writer + round-trip check) is a separate step.
"""
from __future__ import annotations

from fbconfig import settings, save as savemod
from fbconfig.datatypes import CATALOG
from fbconfig.model import Signal
from fbconfig.naming import NamingScheme
from cli.util import ask_int, ask_str, ask_yesno, ask_menu, Cancelled
from cli.views import signals_text


def _choose_direction(model):
    i = ask_menu("Direction", ["In", "Out"])
    if i is None:
        return None
    return model.inp if i == 0 else model.out


def _valid_ip(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


# --------------------------------------------------------------- add signals
def do_add(model, cfg):
    iface = _choose_direction(model)
    if iface is None:
        return
    if iface.free_bytes <= 0:
        print(f"  ERROR: {iface.direction} is full "
              f"({iface.used_bytes}/{iface.max_bytes} bytes). Free space first.")
        return

    where = ask_menu("Where", ["Append at end", "Insert before a signal #"])
    if where is None:
        return
    if where == 0 or not iface.signals:
        index = len(iface.signals)
    else:
        index = ask_int("Insert before signal #", 0, len(iface.signals) - 1)
        print("  Note: inserting shifts the byte offset of following signals "
              "(their names/UUIDs are kept).")

    types = list(CATALOG.keys())
    ti = ask_menu("Data type", types)
    if ti is None:
        return
    dtype = types[ti]
    dt = CATALOG[dtype]
    free = iface.free_bytes

    mode = ask_menu("How to add", ["Separate numbered signals", "Single array signal"])
    if mode is None:
        return

    if mode == 0:  # separate numbered signals
        size_each = 1 if dt.key == "bit" else dt.size
        max_n = free // size_each
        if max_n < 1:
            print(f"  ERROR: one {dtype} needs {size_each} byte(s), only {free} free.")
            return
        unit = "8-bit signals" if dt.key == "bit" else f"{dtype} signals"
        print(f"  Free: {free} byte(s)  ->  up to {max_n} {unit} fit.")
        count = ask_int(f"How many {dtype} signals", 1, max_n)
        prefix = ask_str("Name prefix", default=f"{iface.direction}_{dtype}_")
        start = ask_int("Numbering start value", 0, 99999, default=cfg["naming"]["start"])
        digits = ask_int("Numbering digits (zero-pad: 1=0,1  2=00,01)", 1, 6,
                         default=cfg["naming"]["digits"])
        cfg["naming"] = {"start": start, "digits": digits}
        cfg["last_type"], cfg["last_mode"] = dtype, "single"
        settings.save(cfg)
        scheme = NamingScheme(prefix, start, digits)
        arr = 8 if dt.key == "bit" else 1
        for i in range(count):
            iface.insert(index + i, Signal(scheme.name(i), dtype, array_elements=arr))
        print(f"  Added {count} x {dtype}. {iface.direction}: "
              f"{iface.used_bytes}/{iface.max_bytes} used, {iface.free_bytes} free.")
    else:  # single array signal
        if dt.key == "bit":
            nbytes = ask_int("Array length in BYTES (x8 bits each)", 1, free)
            arr = nbytes * 8
        else:
            arr = ask_int(f"Array length (number of {dtype})", 1, free // dt.size)
        name = ask_str("Signal name", default=f"{iface.direction}_{dtype}_array")
        cfg["last_type"], cfg["last_mode"] = dtype, "array"
        settings.save(cfg)
        iface.insert(index, Signal(name, dtype, array_elements=arr))
        print(f"  Added array '{name}' ({dtype} x{arr}). {iface.direction}: "
              f"{iface.used_bytes}/{iface.max_bytes} used, {iface.free_bytes} free.")


# --------------------------------------------------------------- other edits
def do_delete(model):
    iface = _choose_direction(model)
    if iface is None or not iface.signals:
        print("  Nothing to delete.")
        return
    idx = ask_int("Delete signal #", 0, len(iface.signals) - 1)
    s = iface.remove(idx)
    print(f"  Deleted '{s.name}'. {iface.direction}: {iface.free_bytes} free now.")


def do_rename(model):
    iface = _choose_direction(model)
    if iface is None or not iface.signals:
        print("  No signals.")
        return
    idx = ask_int("Rename signal #", 0, len(iface.signals) - 1)
    iface.signals[idx].name = ask_str("New name", default=iface.signals[idx].name)
    print("  Renamed.")


def do_size(model):
    iface = _choose_direction(model)
    if iface is None:
        return
    print(f"  {iface.direction}: max {iface.max_bytes}, used {iface.used_bytes}.")
    new = ask_int("New total byte count (max)", iface.used_bytes, 1490,
                  default=iface.max_bytes)
    iface.max_bytes = new
    print(f"  {iface.direction} max set to {new} ({iface.free_bytes} free).")
    print("  (Size change is applied to all 3 files when saving.)")


def do_save(state):
    model, paths = state.model, state.paths
    if paths is None:
        print("  No project paths - cannot save.")
        return
    print("\n  About to write:")
    print(f"    {paths.sycon_xml}")
    if paths.val3_xml:
        print(f"    {paths.val3_xml}")
    if paths.nxd:
        print(f"    {paths.nxd}")
    print(f"  In {model.inp.used_bytes}/{model.inp.max_bytes} bytes, "
          f"Out {model.out.used_bytes}/{model.out.max_bytes} bytes.")
    print("  A timestamped ZIP backup is created first.")
    if not ask_yesno("  Proceed with save?", default=False):
        print("  Cancelled.")
        return
    res = savemod.save(model, paths)
    print(f"\n  Backup : {res.backup}")
    for p in res.written:
        print(f"  Wrote  : {p}")
    if res.verified:
        print("  Round-trip self-check: OK (re-read matches the model).")
        print("  IMPORTANT: validate in SyCon.net before downloading to the robot.")
    else:
        print("  Round-trip self-check: FAILED - change is UNVERIFIED:")
        for p in res.problems:
            print(f"    - {p}")
        print("  The backup lets you restore. Do NOT use these files until checked.")


def do_general(model):
    d = model.device
    which = ask_menu("Edit general data",
                     [f"Node ID  (current: {d.node_id})",
                      f"Card IP  (current: {d.ip or '-'})",
                      f"Network name  (current: {d.node_name or '-'})"])
    if which == 0:
        d.node_id = ask_int("New Node ID", 1, 239, default=d.node_id or 1)
    elif which == 1:
        while True:
            ip = ask_str("New Card IP (a.b.c.d)", default=d.ip)
            if _valid_ip(ip):
                d.ip = ip
                break
            print("  Invalid IP address.")
    elif which == 2:
        d.node_name = ask_str("New network name", default=d.node_name)
    print("  Updated.")


# --------------------------------------------------------------- menu loop
def edit_menu(state):
    model = state.model
    cfg = settings.load()
    while True:
        i, o = model.inp, model.out
        print("\n" + "-" * 60)
        print("  EDIT  |  "
              f"In {i.used_bytes}/{i.max_bytes} ({i.free_bytes} free)   "
              f"Out {o.used_bytes}/{o.max_bytes} ({o.free_bytes} free)")
        print("-" * 60)
        print("   [a]  Add data type(s)")
        print("   [d]  Delete a signal")
        print("   [r]  Rename a signal")
        print("   [s]  Change interface size (total bytes)")
        print("   [g]  Edit general data (Node ID / IP / network name)")
        print("   [v]  Preview signals")
        print("   [w]  Save to disk (backup + write + verify)")
        print("   [b]  Back to main menu")
        c = input("  edit> ").strip().lower()
        if c == "b":
            return
        try:
            if c == "a":
                do_add(model, cfg)
            elif c == "d":
                do_delete(model)
            elif c == "r":
                do_rename(model)
            elif c == "s":
                do_size(model)
            elif c == "g":
                do_general(model)
            elif c == "v":
                print(signals_text(model))
            elif c == "w":
                do_save(state)
            else:
                print("  Unknown option.")
        except Cancelled:
            print("  Cancelled.")
