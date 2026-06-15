# SyCon.net Projekt-Struktur — Befunde (Belegung erhalten ohne Neukonfiguration)

Ziel: Bytezahl / Signal-Aufteilung ändern, **ohne** dass SyCon die Belegung löscht.
SyCon-Export/Import und Modul-Anhängen sind beim CIFX RE/PLS DTM **nicht** möglich
→ einziger Weg: Datei direkt editieren.

## Datei-Architektur

- `J207J208_Powerlink_NETX_51_RE_PLS.spj` (16 KB, OLE2)
  Streams: `ConfigXml` (Topologie-Index), `Hardware` (40-B-Zeiger), `Configuration`.
  **Enthält KEINE Größen-/Belegungsdaten.** Nur Projektrahmen + Verweis auf Ordner.
- `J207J208_Powerlink_NETX_51_RE_PLS\_S129\SYCON_net.xml` (137 KB, **reines UTF-8-XML**)
  → enthält den `<BinData dt:dt="bin.hex">`-Blob (68'052 Bytes nach Hex-Decode).
  **Hier liegt die komplette Belegung.** Das ist das Editier-Ziel.
- `..\..\configs\hilscher\J207J208.nxd` / `.xml` = **generierte Exporte** (Quelle ist
  SYCON_net.xml), nicht direkt editieren.

## Prüfsummen

- `<BinData>`-Tag hat nur `dt:dt="bin.hex"` — **keine Länge, keine CRC**.
- Im ganzen SYCON_net.xml **kein** Prüfsummen-Attribut.
- → Die kritische „Phase-3"-Hürde (Checksumme) entfällt für die XML-Ebene.

## Blob-Aufbau (68'052 Bytes)

1. **0–16'717:** 27 längen-prefixierte Records, Format `<u32 Länge><UTF-16LE-String><00 00>`,
   wobei **Länge den 2-Byte-Terminator einschließt**.
   - Rec 0–12: System-Tag-GUIDs
   - Rec 14: großes `<Protocol>`-Schema (Parameter-Defaults inkl.
     `INPUT_LENGTH` default=104 @blob-off 6972, `OUTPUT_LENGTH` default=104 @8820,
     `NODE_ID` default=8, limits 0..1490 bzw. 1..239)
   - Rec 22: Topologie-`<Module>` (simple Sicht: ein `byte[104]` je Richtung)
2. **ab 16'718:** **eingebettetes OLE2-Dokument** (Sig D0CF11E0…), größenunabhängig
   BYTE-IDENTISCH. Nur Stream `CommandTable` (252 B, trivial). ~2560 B groß.
3. **detaillierter Block (Anker `<Module  systemTag="`, hier Byte 19022):** ist ein
   weiterer **längen-prefixierter Record**: `<u32 Länge @ Anker-4><Detail-XML (UTF-16)
   … \0\0>`. Länge = Bytezahl ab Anker INKL. abschließendem \0\0.
   ⚠️ KRITISCH: Wird der Detailblock geändert, MUSS dieses u32-Längenfeld
   aktualisiert werden — sonst „signal table context can not be loaded" in SyCon.
   Der Detailblock selbst ist reiner UTF-16-Text ohne weitere interne Längenfelder.
   Der Topologie-Modul-systemTag (Records) == detaillierter-Modul-systemTag (müssen
   übereinstimmen).

## Belegung (Prozessabbild) — vollständig dekodiert

Pro Richtung (In = signalType "output", Out = signalType "input"), 104 Bytes:

| Typ      | Anzahl | Bytes | Byte-Lage |
|----------|--------|-------|-----------|
| `bit`    | 8 (à 8 Bit) | 8  | 0–7   |
| `word`   | 16     | 32    | 8–39  |
| `real32` | 16     | 64    | 40–103|

Pro `<Signal>`:
- `displayName` (z.B. `In_Word_3`)
- `signalType` output (In) / input (Out)
- `signalAccessPath` = "Byte.Bit"
- `dataType` = bit | word | real32
- `arrayElements` (bit: 8, word/real: 1)
- Property `6100` type=8 = systemTag-GUID (16 B base64)
- Property `6103` type=19 = **Bit-Offset** (LE uint32, base64) — regelmäßig, generierbar

Aktuelle Belegung gesichert: `.claude\work\belegung_current.xml`

## Was eine Größenänderung erfordert (konsistent zu halten)

1. `INPUT_LENGTH` / `OUTPUT_LENGTH` im `<Protocol>`-Record (Wert + Record-Länge)
2. Topologie-`<Module>` (moduleType-Name "104 Bytes In" + arrayElements 104)
3. Detailliertes Prozessabbild im eingebetteten OLE2 (Signalliste neu generieren)
4. **Eingebettetes OLE2 neu aufbauen** (Stream-Größe in Directory + FAT/Sektorketten),
   da sich die Byte-Länge der Signalliste ändert → der einzig anspruchsvolle Teil.

## Offener kritischer Punkt

Der eingebettete OLE2-Container muss beim Schreiben strukturell gültig bleiben.
Empfohlene Absicherung: SyCon erzeugt das gültige Gerüst (Größe ändern + speichern),
das Skript spritzt nur die generierte Belegung in das eingebettete OLE2.
Jede Variante VOR Einsatz mit SyCon.net gegenprüfen.
