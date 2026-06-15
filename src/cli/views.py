"""Rendering of model data as text (tables/overview). Shared by main menu and editor."""
from __future__ import annotations
from fbconfig.datatypes import by_sycon


def render_table(headers, rows, aligns=None) -> str:
    cols = len(headers)
    aligns = aligns or ["<"] * cols
    widths = [len(str(headers[c])) for c in range(cols)]
    for r in rows:
        for c in range(cols):
            widths[c] = max(widths[c], len(str(r[c])))

    def sep():
        return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells):
        return "|" + "|".join(
            " " + format(str(cells[c]), f"{aligns[c]}{widths[c]}") + " " for c in range(cols)
        ) + "|"

    out = [sep(), line(headers), sep()]
    out += [line(r) for r in rows]
    out.append(sep())
    return "\n".join(out)


def overview_text(model, paths) -> str:
    d = model.device
    L = ["", "=" * 60, f"  Fieldbus configuration: {d.base_name}", "=" * 60]
    L.append(f"  Protocol      : {d.protocol}  ({d.firmware})")
    L.append(f"  Node ID       : {d.node_id}")
    L.append(f"  Network name  : {d.node_name or '(none)'}")
    L.append(f"  Card IP       : {d.ip or '(unknown)'}")
    if d.vendor_id is not None:
        L.append(f"  Vendor/Product: 0x{d.vendor_id:08x} / 0x{d.product_code:08x}")
    nxd = model.raw.get("nxd")
    if nxd:
        L.append(f"  .nxd image    : {'MD5 OK' if nxd['md5_ok'] else 'MD5 INVALID'}, "
                 f"{nxd['size']} bytes")
    L.append("-" * 60)
    for iface in model.interfaces():
        L.append(f"  {iface.direction:3} : {iface.max_bytes} bytes max | "
                 f"{iface.used_bytes} used | {iface.free_bytes} free | "
                 f"{len(iface.signals)} signals")
        ts = iface.type_summary()
        if ts:
            L.append("        types: " + ", ".join(f"{k} x{v}" for k, v in ts.items()))
    L.append("-" * 60)
    if paths:
        L.append(f"  Project : {paths.spj}")
        L.append(f"  Exports : {paths.export_dir}  "
                 f"[{'nxd' if paths.nxd else '-'} | {'xml' if paths.val3_xml else '-'}]")
    L.append("=" * 60)
    return "\n".join(L)


def signals_text(model) -> str:
    L = [""]
    headers = ["#", "Address", "Bits", "Type", "Elem", "Name"]
    aligns = [">", ">", "<", "<", ">", "<"]
    for iface in model.interfaces():
        L.append(f"  {iface.direction}  -  {iface.used_bytes}/{iface.max_bytes} bytes used,"
                 f"  {iface.free_bytes} free")
        rows = []
        for idx, sig in enumerate(iface.signals):
            off = iface.byte_offset(idx)
            dt = by_sycon(sig.sycon_dtype)
            sz = sig.size
            addr = str(off) if sz == 1 else f"{off}-{off + sz - 1}"
            bits = f"0-{sig.array_elements - 1}" if dt.key == "bit" else ""
            rows.append([idx, addr, bits, dt.key, sig.array_elements, sig.name])
        if iface.free_bytes:
            ff, ft = iface.used_bytes, iface.max_bytes - 1
            addr = str(ff) if ff == ft else f"{ff}-{ft}"
            rows.append(["", addr, "", "(free)", "", f"{iface.free_bytes} unconfigured byte(s)"])
        L.append(render_table(headers, rows, aligns))
        L.append("")
    return "\n".join(L)
