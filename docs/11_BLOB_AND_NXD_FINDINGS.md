# Blob (SyCon-Projekt) & .nxd — Speicherstruktur (Befunde 2026-06-13)

Quelle: Analyse der Roh-Templates + SyCon-Screenshots in `Desktop/templates/` (PowerLink,
EtherCAT, EtherCAT_FSOE, EthernetIP, Profinet, ProfiSafe) und 60+ echter Configs in
`<robot-data-dir>`. **Keine Hilscher-Doku vorhanden** — alles empirisch.

## Grundprinzip
Das **SyCon-Projekt (`SYCON_net.xml` `<BinData>`-Blob) ist der Master**; `.nxd`,
`_nwid.nxd` und die Val3-`<base>.xml` werden daraus erzeugt. Wer ohne SyCon editiert,
muss konsistent in ALLE betroffenen Dateien schreiben.

## Der Blob = OLE2/CFB-Compound-File
`[4-Byte u32 Länge][OLE2/CFB]` (Magic `d0cf11e0a1b11ae1` @4; mit `olefile` lesbar nach
Strippen der 4 Byte).
- **POWERLINK**: kleines OLE2, das Detail (Signaltabelle) wird **danach** angehängt
  (`bytes_after_detail≈0`). Writer baut Detail neu + patcht Record-Größen → **voll
  editierbar** (add/delete/resize SyCon-valide).
- **EtherCAT / EtherNet-IP / PROFINET**: das ganze Blob ist das CFB; das Detail liegt im
  CFB-Freiraum (per `<Module …systemTag=`-Anker + u32-Länge @anchor-4 gefunden). Das
  **Geräte-Modell** steckt in CFB-Streams (`ECATDataModelBasic`, `DeviceType`,
  `CachedSlave/*`; EIP/PROFINET analog). Byte-Splice des Details korrumpiert das CFB
  strukturell **nicht** (olefile öffnet es), aber die Geräte-Modell-Streams werden bei
  Add/Delete/Resize nicht mit-aktualisiert → SyCon „Gerät kann nicht erzeugt werden".
  **Rename ist sicher** (Signalmenge/Modell unverändert) → aktuell rename-only gegated.

## Die .nxd-Dateien (netX DBM)
Pro Export bis zu **3 Download-Dateien**: `<base>.nxd` (Haupt-Config),
`<base>_nwid.nxd` (**Netzwerk-Identität**, nur PROFINET/EIP/ProfiSafe), Val3 `<base>.xml`.
Jede `.nxd` hat **MD5 @0x54 über `data[136:]`** (auch `_nwid`).

`.nxd` = **netX-DBM mit BENANNTEN Feldern** (nicht Roh-Offsets!). Patchen per Feldname:
- **Skalar-Felder** am Datei-Ende: `<name>\x00 <u32 byteLen> <value> <record-meta>`.
  - `ipIpAddress` / `ipNetMask` / `ipGatewayIp` (je 16-Byte-Wert) — im `_nwid.nxd`.
  - `ulWatchdogTime`, `bSystemStart` (Automatic/by Application) — im `main.nxd`
    (`CHANNEL_SETTING`/`SETUP`).
  - `TypeOfStation`, `SUBMODULES`/`Subslot` (PROFINET-Slots), `usVendorID`/`usProductCode`
    (EIP) — im `main.nxd`.
- **Record-Tabelle + String-Heap** (Datenbereich ab @136): der PROFINET-**Stationsname**
  als längen-präfixierter String (`0b00`+„netx51repns").

## Wo jede Einstellung liegt (Schreibziele)
| Einstellung | Dateien |
|---|---|
| IP / Netmask / Gateway | `_nwid.nxd` + Blob + Val3 |
| PROFINET Stationsname (DCP/DNS) | `_nwid.nxd` + `main.nxd` (`TypeOfStation`) + Blob + Val3 |
| PROFINET Endian (Big/Little Prozessdaten) | Blob „Device-Einstellungen" + `main.nxd` |
| Watchdog (`ulWatchdogTime`) / Anlauf (`bSystemStart`) | `main.nxd` + Blob |
| EIP Transferformat (run/idle header) / Assembly-Größe | Blob + `main.nxd` + configMD5 |
| PROFINET Slots/Module | `main.nxd` (`SUBMODULES`/`Subslot`) + Blob |

## Empirisch verifizierbar vs. nicht
- **Verifizierbar (sicher umsetzbar)**: PROFINET-Stationsname (echter Wert da), Watchdog
  (1000 ms = 0x3E8), Anlauf (beide Texte da).
- **NICHT verifizierbar ohne Beispiel/Doku** (nicht raten!): IP-Kodierung (alle 60 echten
  `_nwid.nxd` haben IP 0.0.0.0 — PROFINET-IP kommt per DCP vom PLC über den Stationsnamen),
  Endian-Flag-Wert (nur Big-Endian-Beispiele). Bräuchte 2 SyCon-Diff-Dateien
  (eine mit gesetzter IP, eine mit Little Endian) oder die Hilscher-DBM/PNS/EIS-Doku.

## Warum PROFINET anders ist
PROFINET ist per **GSDML slot/modul-basiert**: I/O entsteht durch Stecken von Modulen aus
einem Katalog (1/2/3/4/8/12/16/20/32/64 Byte **Eingang**/**Ausgang**, je mit Modul-ID,
z.B. 0x00000002) in Slots (max 256). Bildgröße = Summe der Module. Modul-Benennung
INVERTIERT (Ausgang-Modul = Controller-Input/IB). Man kann KEINE „N Bytes" deklarieren —
die Module müssen real als Submodule in Blob+nxd existieren. (EtherCAT=PDO-Mapping,
EIP=Assembly-Größe, POWERLINK=flaches INPUT/OUTPUT_LENGTH.)

## Umsetzungsstufen
- **Stufe 1 (skalar, in-place, kein Längen-Shift → CFB bleibt heil)**: Stationsname,
  Watchdog, Anlauf, Endian (PROFINET-Skalare komplett, s.u.); IP nur EIP.
- **Stufe 2 (strukturell, braucht CFB-/nxd-Submodul-Umbau + SyCon-Runden)**: PROFINET
  Slot-Editor, EIP-Assembly-Resize, EtherCAT-PDO-Resize, sowie Add/Delete für die drei
  CFB-Protokolle.

## PROFINET-Skalarfelder — bewiesen (Diff SyconTest/Sycon vs FieldbusConfigEditor)
Alle gegen ein echtes SyCon-Diff-Paar verifiziert:
- **Stationsname**: fester null-gefüllter Puffer `<u16 Länge><ASCII><Nullen>`. In `_nwid.nxd`
  @216 (Patch + MD5 = **byte-für-byte identisch mit SyCon**). main.nxd unberührt. Val3:
  `]&lt;NAME&gt;` + `stationAddress="Adr NAME"`. Blob: `deviceNo="NAME"` (XML, außerhalb
  BinData) + UTF-16-Puffer im CFB (das richtige der 2 Vorkommen — nicht das „DIM 24"-Feld).
- **Watchdog**: u32 ms im main.nxd-Wertbereich (CHANNEL_SETTING). Blob: kommt 1× vor.
- **Anlauf** (`bSystemStart`): 1 Byte direkt VOR dem Watchdog — **0 = Automatisch durch
  Gerät, 1 = Gesteuert durch Applikation**.
- **Endian**: **Big = `0x01`, Little = `0x02`** als **Byte pro Submodul** (68-Byte-Records im
  main.nxd-Wertbereich) **+** `byteOrder="big"/"little"` in der Val3.
- **🔑 configMD5 = MD5 des main.nxd** (@0x54 über data[136:]). Jede main.nxd-Wertänderung
  (Endian/Watchdog/Anlauf) MUSS `configMD5="…"` in Val3 + Blob nachziehen.
- **IP/Maske**: bei PROFINET NICHT konfigurierbar (DCP, controller-seitig; SyCon sperrt es).
- Robustes Patchen: Felder über Deskriptor/Record-Marker lokalisieren, NICHT per festem
  Offset (Wert- und Deskriptor-Sektion sind getrennt, per Index verknüpft).

## PROFINET-Modul/Slot-Editor — Spezifikation (Stufe 2)
Zwei Ebenen in der Tabellenansicht:
- **Modul-Ebene (Slots)**: Modul anlegen = Richtung + Katalog-Größe (1/2/3/4/8/12/16/20/32/64
  Byte In/Out, je eigene Modul-ID). Als farbiges Band dargestellt. **Slot-Nummer editierbar**
  (Default aufsteigend, eindeutig, muss zum PLC passen; Slot 0/Ports fix). Löschen/ordnen.
- **Signal-Ebene**: Slots mit Signalen füllen; **ein Signal liegt immer ganz in EINEM Modul**
  (nie über eine Grenze; real32 → Modul ≥4 Byte). Verschieben nur, wenn es ins Modul passt
  (live grün/rot). Modell-Logik existiert: `_pn_module_ranges`/`pn_add`/`pn_relocate`.
- Modul-Komposition ist ein **Vertrag mit dem PLC** (2×1-Byte ≠ 1×2-Byte: andere Slots/IDs/
  IOPS/Konsistenz). Auto-Komposition möglich, aber Modul-Liste anzeigen (PLC nachbauen) und
  nie einen Mehrbyte-Typ splitten.
- Schreibt `SUBMODULES`/`Subslot` + Modul-IDs in `main.nxd` + CFB-Blob → braucht ein
  PROFINET-Diff-Paar mit mehreren Modulen zum byte-genauen Ableiten.
