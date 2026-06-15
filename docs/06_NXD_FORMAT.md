# 06 — Format der J207J208.nxd (netX-Chip-Image)

Vollständig geknackt und **byte-validiert** gegen SyCon-Export. Basis-Samples:
robot 1 Original (Node 8, 104) aus Backup `Roboter\Val3\EXAMPLE 20260611.zip`,
robot 2 (Node 9, 104) aus `TX2_60_2\usr\configs\hilscher\`.

## Gesamtaufbau
- Magic `2E 4E 58 44` = ".NXD" @0.
- **Header [0 : 136]**, **Daten [136 : Dateiende]**.
- Dateigröße fix **3456 B** (für 104 UND 120 — Größe ändert nichts an Struktur).

## Header-Felder (Offsets dezimal/hex)
| Offset | Größe | Bedeutung |
|---|---|---|
| 0x40 | u32 | Version/Flags = 0x00010000 |
| 0x44 | u32 | Header-/Datenoffset = 136 |
| 0x48 | u32 | Dateigröße = 3456 |
| 0x4C | u32 | = 136 |
| **0x54** | **16 B** | **MD5( Daten[136:Ende] )** — reproduzierbar |
| 0x7C | u32 | Metadaten (Timestamp/Serial, NICHT MD5-relevant) |
| 0x80 | u32 | Metadaten |

## Konfig-Felder im Datenbereich (Offsets dezimal)
| Offset | Typ | Bedeutung | Beispielwert |
|---|---|---|---|
| 264 | u32 | WATCHDOG_TIME | 1000 |
| 268 | u32 | VENDOR_ID | 0x44 |
| 272 | u32 | PRODUCT_CODE | 0x1E |
| 292,296,300,308,312 | u32 | LOSS-Thresholds | 15 |
| **324** | **u16** | **INPUT_LENGTH (Bytes In)** | 104 → 120 |
| **326** | **u16** | **OUTPUT_LENGTH (Bytes Out)** | 104 → 120 |
| 328.. | ASCII | DNS-Node-Name (0-terminiert; robot1 leer) | "" / "robot2" |
| 360..363 | 4 B LE | IP-Adresse | 192.0.2.x |
| **364** | **byte** | **NODE_ID** | 8 |

## Wichtige Erkenntnisse
- Die **PDO-Länge steckt ausschließlich in den zwei u16-Feldern @324/@326**.
  104 kommt im File NIRGENDS sonst vor; Dateigröße unverändert → keine
  Struktur-/Mapping-Tabelle, die mit der Größe skaliert.
- Die einzige Prüfsumme ist MD5 @0x54 über [136:Ende] — bei jeder Änderung neu rechnen.
- Diff zweier gültiger Files (robot1 vs robot2, beide 104) = NUR Node(364), Name(328),
  IP(360), MD5(84..99). Bestätigt die Feldzuordnung.
- **Validierung:** Aus robot 1 Original (104) durch Setzen @324/@326=120 + MD5 erzeugte
  Datei ist **byte-identisch** zur SyCon-erzeugten Flach-120-.nxd. → Erzeugung bewiesen.

## Generator
`gen_nxd.py <in_len> <out_len>` — Basis robot 1 Original-.nxd, setzt Längen, MD5 neu.
Roboterspezifische Werte (Node/Name/IP) bleiben unangetastet → robot 2 NICHT als
Vorlage verwenden (hat Node 9 / Name "robot2" / andere IP).
