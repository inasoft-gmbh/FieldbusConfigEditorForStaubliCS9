# Architecture & Developer Guide — Fieldbus Config Editor

Self-contained knowledge base for this project. Read together with the format
docs in this folder (`STRUCTURE.md`, `06_NXD_FORMAT.md`, `07_VAL3_XML_FORMAT.md`)
and the cross-session notes in `../memory/`.

## 1. Purpose
Create/edit Hilscher netX fieldbus configurations (SyCon.net projects) for Stäubli
robots **without SyCon**, while preserving the byte allocation (bool/word/real…).
Three on-disk files must stay consistent:

```
<robot>/usr/fieldbus/<name>/<Base>.spj            OLE2 project frame (topology index only)
<robot>/usr/fieldbus/<name>/<Base>/_S129/SYCON_net.xml   DTM store (the configuration, hex blob)
<robot>/usr/configs/<name>/<ExportBase>.xml       Val3 process image (named symbols)
<robot>/usr/configs/<name>/<ExportBase>.nxd       compiled netX image (goes to the chip)
```
Export base/path come from the loader `<Base>.xml` (`BaseNameForExportedFiles`,
`PathToExportedFiles`). The `.spj` itself holds no sizes/belegung.

## 2. Design principle: core vs UI
`src/fbconfig/` is the **core** — all knowledge, NO console/GUI I/O. The console
(`src/cli/`) and any future GUI are thin clients of the same core. This is why a
GUI can be added later without touching the logic.

```
src/fbconfig/                core (no UI)
  datatypes.py   DataType catalogue (authoritative SyCon list, see §5)
  model.py       ConfigModel: DeviceInfo + Interface(In/Out) + Signal; byte accounting
  naming.py      NamingScheme: prefix + start + digit padding -> auto-numbered names
  sycon.py       READ SYCON_net.xml (blob -> device + signals)
  nxd.py         READ .nxd (node/name/ip/lengths/MD5)
  project.py     discover() robot folder -> ProjectPaths; load() -> ConfigModel
  settings.py    persisted prefs (settings.json: numbering, last type/mode)
  writers.py     WRITE all three files from the model (clone templates from originals)
  backup.py      timestamped ZIP backup
  save.py        orchestrate: backup -> write -> round-trip verify -> changes.log
src/cli/                     console front-end (guided menus, English)
  folderpick.py  native folder dialog (tkinter; no GUI commitment)
  util.py        input helpers + Cancelled exception ('c' aborts any prompt)
  views.py       text rendering: overview + signals table (shared)
  edit.py        guided edit menu (add/insert/delete/rename/size/general/save)
  app.py         main menu loop + state
run.py                       entry point: python run.py [robot_folder]
prototype/                   original RE scripts (proven; basis for writers.py)
docs/  memory/  samples/  tests/
```

## 3. Data flow
`project.discover(robot)` → finds `.spj`s, reads loader xml for export base/path.
`project.load(paths)` → `sycon.load()` (protocol, lengths, signals) + `nxd.read()`
(authoritative node/name/ip on the card) → `ConfigModel`.
Edit menu mutates the model (offsets are always derived from signal order).
`save.save(model, paths)` → backup → `writers.write_sycon/val3/nxd` → re-read & verify.

## 4. The model (model.py)
- `Signal(name, sycon_dtype, array_elements, systemtag)`. `size` = `bit`→
  `array_elements//8`, else `type_size * array_elements`. systemtag (UUID) is
  generated if empty and **preserved** across edits; new signals get fresh ones.
- `Interface(direction, max_bytes, signals)`. Byte offset of a signal = sum of
  preceding sizes. `free_bytes = max_bytes - used_bytes`. `insert()` raises
  `ValueError` if it would exceed `max_bytes` (this is the "too many bytes" guard).
- Inserting in the middle keeps following signals' names/UUIDs; only their byte
  offset shifts (recomputed automatically).

## 5. Data type catalogue (authoritative — from SyCon "Edit Signal" dialog)
`bit`(1B/8 bits), `byte`(1B), `signed8`(1B), `unsigned8`(1B), `word`(2B),
`signed16`(2B), `unsigned16`(2B), `dword`(4B), `signed32`(4B), `unsigned32`(4B),
`real32`(4B). The catalogue key == the `dataType` string in the XML. `bit` Count is
in BITS (multiples of 8); a bit signal can span several bytes (arrayElements=bits).

## 6. Editing rules (implemented in cli/edit.py)
- Free bytes always shown; count is limited to what fits; over-limit is blocked.
- Add modes: **separate numbered signals** OR **single array signal** (both offered).
- Auto-numbering: prefix + start value + digit padding; start/digits remembered
  in settings.json.
- General data editable: total byte count (Interface.max_bytes), Node ID, Card IP,
  network name.
- Any prompt: type `c` to cancel (raises `Cancelled`, caught centrally).

## 7. Writers (writers.py) — how each file is produced
Use the EXISTING file as a skeleton and clone per-dtype signal **templates** from it
(proven byte format), substituting name/UUID/offset/arrayElements. Then verify by
re-reading.
- **SYCON_net.xml**: parse the length-prefixed UTF-16 records, patch INPUT/OUTPUT_
  LENGTH + NODE_ID + topology module size strings, **re-emit records with correct
  u32 length prefixes** (so digit-count changes are safe). Embedded OLE2 is copied
  verbatim. Detail block (signal table) is rebuilt from templates; the u32 length
  field at `anchor-4` is updated. Anchor = `<Module  systemTag="` (two spaces).
- **J207J208.xml**: patch `stationAddress="Addr N"` + module size names; replace each
  module's signal list from templates.
- **.nxd**: patch u16 @324 (INPUT_LENGTH), u16 @326 (OUTPUT_LENGTH), byte @364
  (NODE_ID), IP @360 (LE), name @328; recompute MD5 @0x54 over data[136:]. Proven
  byte-identical to SyCon's export.

## 8. Save safety (save.py)
1. timestamped ZIP backup → `usr/fieldbus/_backups/`.
2. write all three files.
3. round-trip self-check: re-read from disk, compare to the model (signal layout,
   offsets, sizes, node, .nxd MD5). `verified` only if it all matches.
4. append `changes.log`.
Always validate in SyCon.net before downloading to the robot (the app says so).

## 9. How to extend
- **New bus protocol**: the SyCon/netX framework (OLE2, blob records, length field,
  .nxd header+MD5) is very likely shared. Protocol-specific parts: parameter set,
  module catalogue, .nxd field offsets. Add a `protocols/<name>.py` descriptor and,
  if offsets differ, parametrize sycon/nxd readers/writers. **Needs a sample project
  per protocol** (drop in `samples/`).
- **GUI (PySide6)**: build a new front-end package that imports `fbconfig` and calls
  the same `project`, model editing API, and `save`. No core changes needed.
- **New configuration from scratch**: planned as template-based (keep SyCon-made flat
  skeletons per protocol/size; "new" starts from the nearest template).

## 10. Test data (real, on this machine)
- `…/TX2_60_2` — clean original robot (node 9, 104 bytes, name "robot2").
- `…/TX2_60_1` — worked-on robot (multiple project copies in usr/fieldbus/).
- Backups: `…/Roboter/Val3/EXAMPLE 20260611.zip` (pre-work, node 8, 104).
Test writers against a SANDBOX COPY only — never the live robot files.
`tests/test_save.py` does exactly this (copy → save unchanged → edit → verify).
