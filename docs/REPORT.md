# MASTER-REPORT — Hilscher CIFX RE/PLS Größenänderung ohne SyCon

**Projekt:** Stäubli TX2-60 (Roboter 1), Hilscher netX-51 CIFX RE/PLS, POWERLINK,
Node 8, Projekt J207J208. Stand: 2026-06-12. Sprache: Deutsch.

## Aufgabe
Bytezahl (In/Out) der Fieldbus-Konfiguration ändern, **ohne** dass die manuelle
Byte-Belegung (bool/word/real) verloren geht. SyCon.net löscht sie beim Resize, und
Export/Import sowie Modul-Anhängen sind bei diesem DTM nicht möglich → die drei
betroffenen Dateien müssen direkt erzeugt werden. Validierungsbeispiel: 104 → 120 Byte.

## Die drei betroffenen Dateien (Datenfluss)
SyCon-Projekt (`.spj` + Ordner mit `SYCON_net.xml`) → erzeugt per Export →
`configs\hilscher\J207J208.xml` (Val3-Prozessabbild) + `J207J208.nxd` (netX-Chip-Image).

| Ebene | Datei | Enthält | Editiert |
|---|---|---|---|
| 1 | `…\_S129\SYCON_net.xml` | DTM-Daten inkl. Belegung (Hex-Blob) | ja (Injektion) |
| 2 | `configs\hilscher\J207J208.xml` | Val3-Symboltabelle (benannte Signale) | ja (generiert) |
| 3 | `configs\hilscher\J207J208.nxd` | kompiliertes netX-PDO-Image | ja (Längen+MD5) |

`.spj` (16 KB OLE2) enthält nur Topologie-Index, KEINE Größen/Belegung → unverändert.

## Ebene 1 — SYCON_net.xml (Belegung) — Details: STRUCTURE.md
- Editier-Ziel ist der `<BinData dt:dt="bin.hex">`-Hexblob (68 KB). **Keine Prüfsumme**
  auf XML-Ebene.
- Blob: [27 längen-prefixierte UTF-16-Records: Protocol-Schema mit INPUT/OUTPUT_LENGTH,
  NODE_ID, Topologie-Modul] + [eingebettetes OLE2, größenunabhängig BYTE-IDENTISCH,
  nie anfassen] + [**Detail-Record** ab Anker `<Module  systemTag="` (2 Spaces)].
- **KRITISCH:** Direkt vor dem Anker (Anker−4) steht ein **u32-Längenfeld** =
  Bytezahl des Detailblocks inkl. `\0\0`. MUSS beim Ändern aktualisiert werden, sonst
  SyCon-Fehler „signal table context can not be loaded".
- Detailblock = reiner UTF-16-XML-Text der Signalliste. Signal: systemTag,
  signalAccessPath "byte.0"(bit)/"byte"(word/real), Property 6100 = GUID (MS-Binär
  base64), Property 6103 = Bit-Offset (LE u32 base64). Topologie-Modul-systemTag muss
  == Detail-Modul-systemTag.
- **Methode:** Skelett = von SyCon gespeicherte Zielgröße (flach) liefert korrektes
  INPUT/OUTPUT_LENGTH + Topologie + OLE2. Injektion ersetzt die flache Signalliste
  durch die bestehenden Signale VERBATIM (Byte 0..103 byte-identisch → Val3-Mapping
  stabil) + geklonte neue Signale, und setzt das u32-Längenfeld neu.

## Ebene 2 — J207J208.xml (Val3) — Details: 07_VAL3_XML_FORMAT.md
- SyCon-Export mit benannten Signalen (`In_Byte_0`, `In_Word_3`, `In_Real_5`, …),
  die der Stäubli-Code referenziert. Resize plättet ihn (1 Byte-Array statt 40 Signale).
- **Methode:** Format-/Struktur-Vorlage = Roboter 2 (`TX2_60_2`), roboter-1-spezifische
  Kopfwerte gepatcht (Node 8, deviceAccessPath, Modul-systemTags), Signaldaten
  (systemTags/Namen/Offsets/6103) aus Roboter 1s SYCON_net.xml.

## Ebene 3 — J207J208.nxd (Chip) — Details: 06_NXD_FORMAT.md
- `.NXD`-Image: Header [0:136], Daten [136:Ende]; @0x54 (16 B) = **MD5(Daten[136:])**;
  @324/@326 u16 = INPUT/OUTPUT_LENGTH; @364 = NODE_ID; @328 = Name; @360 = IP (LE).
- PDO-Länge steckt NUR in @324/@326 (104 kommt sonst nirgends vor; 104- und 120-Datei
  beide 3456 B).
- **Methode:** Basis = robot 1 Original-.nxd aus Backup (Node 8, leerer Name, IP .123),
  nur @324/@326 → Zielgröße, MD5 neu.

## Validierung (alle bestanden)
- Generator reproduziert 104-Belegung strukturell exakt; GUID-6100 byte-genau.
- Injector Identitäts-Selbsttest byte-exakt (OLD & NEW).
- Injektion 104→120: In/Out je 44 Signale lückenlos, erste 104 Bytes byte-identisch.
- **Projekt in SyCon geöffnet → Belegung vollständig da (vom User bestätigt).**
- **J207J208.nxd BYTE-IDENTISCH zur SyCon-Flach-120-.nxd** (`fieldbus\J207J208.nxd`).
- **J207J208.xml-Gerüst identisch zur SyCon-Flach-120-.xml** (nur Signallisten = Belegung).

## Risiko / Stop-Regeln
- Originaldateien nie überschreiben; Arbeit unter `.claude\work\`.
- Vor Chip-Download: Backup + SyCon-Gegenprüfung. (.nxd ist hier byte-bewiesen.)
- 2-stellige Zielgrößen (<100) nicht getestet (Record-Längen der Größenstrings
  würden sich verschieben; Skelett von SyCon ist aber ohnehin korrekt).

## Artefakte unter .claude\work\
- `STRUCTURE.md` — SYCON_net.xml-Format
- `06_NXD_FORMAT.md` — .nxd-Format
- `07_VAL3_XML_FORMAT.md` — J207J208.xml-Format
- `belegung_gen.py` — Belegungs-Generator (+Selbstvalidierung)
- `spj_inject.py`, `make_modified.py` — SYCON_net.xml Injector/Writer
- `gen_val3_xml.py` — J207J208.xml-Generator
- `gen_nxd.py` — J207J208.nxd-Generator
- `modified_120\` — fertiges SyCon-Projekt (120)
- `nxd\` — Original-.nxd (104) + generierte .nxd (120)
- `configs_120\J207J208.xml` — generiertes Val3-Abbild
- `scripts\` — Analyse-Hilfsskripte

## Künftige Größenänderung (SyCon-frei)
1. In SyCon Zielgröße speichern (flaches Skelett) ODER vorhandenes Skelett nutzen.
2. `make_modified.py` (extra-Aufteilung der Zusatzbytes setzen) → SYCON_net.xml.
3. `gen_val3_xml.py` → J207J208.xml.
4. `gen_nxd.py <in> <out>` → J207J208.nxd.
5. Backup + SyCon-Gegenprüfung, dann aufspielen.
