# 08 — Strukturelle Bearbeitung der bit-granularen Protokolle

Reverse-Engineering-Erkenntnisse zum **strukturellen Editieren** (Add / Delete /
Reorder / Resize) von EtherNet/IP, EtherCAT und PROFINET. Diese Datei lebt im
Repo, damit das Wissen beim Projekt bleibt (auch nach `git clone`).

> **Wichtigste Lektion zuerst (sonst baut man Fehler ein):**
> Der Round-Trip-**Selbstcheck reicht für Strukturänderungen NICHT**. Er trennt
> Signale nach `signalType` (nicht nach Modul) und liest sie aus der **SyCon**-
> Datei (nicht aus der Val3). Damit ist er **blind** für:
> 1. eine verlorene/verschobene **Modulgrenze** (RxPDO/TxPDO) und
> 2. eine **nicht aktualisierte Val3**.
> Beide Fehler hat er als „verified=True" durchgewinkt. Für Strukturänderungen
> IMMER zusätzlich den **harten Check** fahren (siehe unten) **und** eine
> SyCon.net-Gegenprobe machen.

---

## 1. Prozessabbild-Größe in der `.nxd` (pro Protokoll)

Das Prozessabbild hat eine **feste Größe**, die in der `.nxd` steht. Ein Signal
ist nur ein **Name auf einer Adresse** in diesem festen Block → **Add/Delete/
Reorder ändern die `.nxd` NICHT** (md5 bleibt identisch). Nur **Resize** ändert
die Blockgröße und braucht eine neue `.nxd` (Skeleton-Ansatz).

| Protokoll   | In-Größe        | Out-Größe       | Einheit | MD5 |
|---|---|---|---|---|
| POWERLINK   | `.nxd` @324 u16 | @326 u16        | Bytes   | @0x54 über data[136:] |
| EtherNet/IP | `.nxd` @1141 u16| @1181 u16       | **Bits**| @0x54 über data[136:] |
| **EtherCAT**| `.nxd` **@380** u16 | **@408** u16 | **Bytes**| @0x54 über data[136:] |
| PROFINET    | *noch nicht lokalisiert* | — | — | (vermutlich gleich) |

- EtherCAT @380/@408 **validiert über 11 Projekte** (Größen 1, 2, 16, 40, 60, 72,
  79, 88, 108, 136, 208 B, inkl. **asymmetrischer** In/Out 72/40 und 60/72 →
  schließt Vertauschung/Zufall aus). Die `.nxd`-Gesamtgröße variiert stark
  (8 812…56 492 B) → es gibt größenabhängige Abschnitte über die 2 u16-Felder
  hinaus ⇒ **Resize nur per Skeleton** (echte `.nxd` der Zielgröße), nicht durch
  Patchen der 2 Felder.
- EtherNet/IP @1141/@1181 sind bei EtherCAT **0** (protokollspezifisch!).
- PROFINET nutzt netX 51 (anderes Image); Feld muss separat per Diff
  unterschiedlich großer PROFINET-Projekte gesucht werden.

So findet man das Feld: `.nxd` mehrerer **verschieden großer** Projekte einlesen,
für jeden Offset prüfen, ob `u16_LE == In_bytes` (bzw. `*8` für Bits) bei **allen**
Projekten gilt; Schnittmenge der Offsets = das Feld. Asymmetrische Projekte
trennen In von Out.

---

## 2. Modul-Struktur (SyCon-Detail **und** Val3)

| Protokoll   | Module | Belegung | accessPath |
|---|---|---|---|
| EtherNet/IP | **1** (Connect, `moduleAddress="Slot 1"`) | Input- dann Output-Signale gemischt | `byte` bzw. `byte.bit` |
| **EtherCAT**| **2** (RxPDO=Input, TxPDO=Output) | je ein Modul pro Richtung | bit (ae=8)→`byte`, bit (ae<8)→`byte.bit` |
| PROFINET    | **n** (verschachtelt Slot/Subslot, Größen-Container) | je Signal ins Modul seines Byte-Bereichs | **global** je Richtung; bit IMMER `byte.bit`, sonst `byte` |

**Modulgrenze (EtherCAT, kritisch):** Zwischen letztem Input- und erstem Output-
Signal steht **ein einzelner Lead** `\r\n</Module>\n<Module … TxPdo …>`. Im
„Full-Render" darf dieser Lead **nicht** aus dem dtype-Template/per-Signal-ws
kommen (dann wandert/verschwindet er bei Output-Operationen → 1 Modul statt 2).
→ **Leads + Grenze nach POSITION setzen:** `prefix` + Input-Signale (normaler
Intra-Modul-Lead) + **Grenz-Lead** + Output-Signale + `suffix`. Single-Modul
(EtherNet/IP) hat keinen Grenz-Lead → jede Lücke = normaler Lead.

**Diagnose-Signale:** In der **Val3** liegen ~8 Signale (Communication State,
Watchdog, …) **außerhalb** der Datenmodule. Bei der Val3-Regenerierung nur die
Modulinhalte ersetzen, die Außen-Signale **byte-exakt** lassen.

**Signal-Felder** (beide Dateien): `systemTag` (UID — wandert mit dem Signal, der
PLC verlinkt darüber, NICHT über die Adresse), `displayName`, `signalType`
(input/output), `signalAccessPath`, `dataType`, `arrayElements`, Property `6103`
(Bit-Offset, base64 LE u32). EtherCAT: UPPERCASE Modul-GUIDs.

---

## 3. Implementierter Ansatz (EtherCAT, in `protocols/ethernetip.py`)

- **`_build_detail` (SyCon):** Full-Render setzt Leads + Modulgrenze nach Position
  (siehe oben). Body je Signal aus `by_uid` (Selbst-/Identitätsfall, byte-exakt)
  sonst `by_dtype`-Template; Offsets (accessPath, 6100, 6103) werden neu gerechnet.
- **`write_val3`:** generalisiert von „nur Slot 1" auf **jedes Datenmodul nach
  Richtung** (RxPDO=Input, TxPDO=Output; EtherNet/IP 1 gemischtes Modul). Außen-
  Signale unberührt. Identität/Rename weiterhin name-only by-UID (byte-exakt).
- **`.nxd`:** bei Add/Delete/Reorder **verbatim** kopiert (Größe unverändert).
- **Resize (KEIN Skeleton mehr — einfache Byte-Spinbox):** Die Prozessabbild-Größe
  steht in der `.nxd` (@380 In-Bytes, @408 Out-Bytes, uint16 LE). `generic_write`
  ruft `_ethercat_nxd_resized()`, das @380/@408 auf `model.inp/out.max_bytes` setzt
  und die Framework-MD5 (@0x54 über `data[136:]`) neu rechnet — nur ~20 Bytes
  ändern sich, ein No-op-Resize reproduziert die `.nxd` **byte-exakt** (analog
  POWERLINK @324/@326, validiert an 11 Projekten Größe 1..208). `sycon`/`val3`
  bleiben unverändert (keine Signal-Bewegung). GUI: `ResizeForm`-Spinbox wie
  POWERLINK. **In SyCon.net re-validieren** (offen: ob netX ein vergrößertes
  Abbild ohne Skeleton-Level-PDO-Änderung akzeptiert).
- **Add:** `EipAddForm` klont `arrayElements`/Bitbreite aus einem bestehenden
  Signal desselben dtype (per-Bit ae=1 vs. byte-packed ae=8) — nicht hart ae=1.
- **Gating:** `raw['structural']` (Add/Delete/Reorder) und `raw['resizable']`
  (Resize). EtherCAT beide True (sobald `.nxd`-Größe lesbar). PROFINET beide
  False (per-Slot-accessPath nicht reproduzierbar). POWERLINK/EtherNet-IP Default
  `not modular` = True.

---

## 4. Der harte Validierungs-Check (Pflicht bei Strukturänderung)

Nach dem Schreiben einer geänderten Konfiguration **alles Folgende** prüfen
(nicht nur den Selbstcheck):

1. **SyCon-Modulstruktur:** Modulanzahl wie im Original (EtherCAT=2, EtherNet/IP=1);
   beim 2-Modul-Fall: alle `signalType="input"` **vor** der `</Module>`-Grenze,
   alle `output` **danach**.
2. **Val3 ↔ Modell:** je Datenmodul die `systemTag`-Liste == Modell-Signale der
   passenden Richtung **in Reihenfolge**; die ~8 Außen-Signale unverändert.
3. **6103-Offsets:** in der Val3 je Signal `base64→u32 == sig.bit_offset`.
4. **`.nxd`:** bei Add/Delete/Reorder md5 **unverändert**; bei Resize == Skeleton-
   `.nxd` und @-Felder = Zielgröße.
5. **Reload:** Datei neu laden, Signalzahlen/Reihenfolge == Modell.
6. **SyCon.net-Gegenprobe:** eine generierte Datei real öffnen — lädt fehlerfrei,
   Prozessabbild korrekt, keine Lücken/Überlappungen.

**Stand EtherCAT:** Schritte 1–5 über **36/36** ladbare EtherCAT-Projekte für
Delete (inkl. `out[0]`), Reorder (inkl. Output) und Resize-per-Skeleton bestanden.
Schritt 6 (SyCon) steht beim Nutzer aus (Beispiel: `EtherCAT_SyCon_Test/`).

**Stand EtherNet/IP:** 1 Modul (keine Grenze) → strukturell ok; Val3-Regenerierung
re-verifiziert. Harter Sweep + SyCon-Gegenprobe noch ausstehend (siehe Roadmap 09).

**Stand PROFINET:** aktuell nur rename + save (Gating `structural=False`). Die
Struktur ist aber **vollständig reverse-engineert** (siehe §5) — Strukturedit ist
machbar, sobald der slot-fähige Writer steht.

---

## 5. PROFINET-Struktur (RE abgeschlossen, Korrektur der „per-Slot"-Annahme)

Frühere Annahme „accessPath restartet per Slot" war **FALSCH**. Tatsächlich:

- **Verschachtelte Slot/Subslot-Module aus einem Größen-Katalog**: `displayName`/
  `moduleType` = „N Byte Eingang"/„N Byte Ausgang" (N ∈ 8,12,16,32,64,…). Jedes
  logische Modul erscheint **zweimal** (Slot, der einen Subslot enthält), Signale
  liegen im Subslot. Die **Summe der Modulgrößen je Richtung = Prozessabbild-Größe**
  (z. B. 64×3 + 12 + 32 = 236 B). Es gibt **kein** einzelnes Größenfeld in der
  `.nxd`/`_nwid.nxd` (über 6 Größen + asymmetrisch erfolglos gesucht).
- **Richtung ist invertiert benannt**: ein „**Ausgang**"-Modul (Geräte-Ausgang)
  enthält `signalType="input"`-Signale (Controller-Eingang), „**Eingang**" enthält
  `output`. → **`signalType` ist maßgeblich**, nicht der Modulname.
- **accessPath = GLOBALER Byte-Offset je Richtung**, durchgehend (8B-Modul→Byte
  0-7, 16B→8-23, 32B→24-55 …; Bereich 0..size-1). Validiert klein (56 B) und groß
  (236 B, 256 Signale).
- **Format-Regel**: `dataType="bit"` → **immer** `byte.bit` (z. B. `0.0`, `0.7`),
  unabhängig von `arrayElements`; alle anderen Typen → `byte` (z. B. `12`).
  (Das ist der EINZIGE Renderunterschied zu EtherCAT/EtherNet-IP und war die
  ganze frühere Byte-Differenz.)
- **Signal-Platzierung**: ein Signal gehört in das (Sub)Modul seiner Richtung,
  dessen Byte-Bereich seinen Offset enthält. Der n-Modul-Render ist die
  Verallgemeinerung des EtherCAT-2-Modul-Grenzfalls: Modulreihenfolge + Subslot-
  Schachtelung aus dem Skeleton übernehmen, je Modul die Signale der passenden
  Richtung im passenden Byte-Bereich einsetzen.
- **`.nxd`**: bei Add/Delete/Reorder verbatim (feste Slot-Belegung). **Resize** =
  andere Slot-Komposition → nur per **Skeleton** (echtes PROFINET-Projekt der
  Zielgröße); ein Modulkatalog-Generator wäre die Alternative.
- **Safe-Varianten** (PROFIsafe) existieren (z. B. 104/104, 56/56). F-Module als
  unveränderlich behandeln (Roadmap 09 C).

### 5b. Verfeinerung: 6103 ist MODUL-LOKAL, accessPath ist GLOBAL (wichtig!)
Beim Implementieren entdeckt: das Property **`6103` (Bit-Offset) ist je Modul/
Subslot LOKAL (0-basiert)**, der **`signalAccessPath` ist der GLOBALE Byte-Offset**.
Beispiel A1807: das 16-Byte-Modul beginnt bei Byte 8 → sein Block-Signal hat
`accessPath="8"` (global) aber `6103=0` (lokal). Im ersten Modul (Start 0) ist
lokal==global, daher fällt es dort nicht auf.

Folgen für den Writer/Model:
- `parse_signals` legt heute `bit_offset` aus 6103 ab → das ist der **lokale**
  Offset. Zwei Signale verschiedener Module können denselben `bit_offset=0` haben.
  ⇒ Das flache Modell kennt die **globale Position nicht**.
- Signale sitzen an **modul-ausgerichteten Positionen mit LÜCKEN** (z. B. Bytes
  9–23 im 16-Byte-Modul unbelegt). ⇒ **Naives `repack_bits` (dichtes Packen) ist
  FALSCH** für PROFINET.
- Beim Schreiben gilt: `accessPath = global` (= Modulstart + lokal),
  `6103 = lokal` (= global − Modulstart). Modulstart je Richtung = Summe der
  vorherigen Modulgrößen.

**Umgesetzt & validiert (Engine):** `parse_signals(profinet=True)` liest den
**globalen** Offset aus `accessPath`; `_render_by_modules` platziert je Modul nach
Byte-Bereich (accessPath global, 6103 = global − Modulstart); Signale außerhalb
Module (Diagnose/F) bleiben verbatim. `_pn_image_size` = Summe der Modulgrößen je
Richtung = **feste** Image-Größe (max_bytes), NICHT die Signalsumme.

**Wichtige Korrektur zur Korrektur:** Module sind **feste Größen-Container**.
`repack_bits` ERHÄLT zwar die Offsets bei No-Change (Layouts sind dicht gepackt),
aber bei **Delete würde dichtes Repack Signale über Modulgrenzen schieben**
(Block-Signale!). ⇒ **Delete lässt eine LÜCKE** (Signal entfernen, **kein**
Repack); die übrigen behalten ihre Offsets. **Add** muss ein freies Byte
**innerhalb eines Moduls** finden (noch offen). **Add** = freies Byte in einem Modul finden (`pn_free_runs`/`pn_add`, kein Repack).
Validiert (harter Check: Module + Val3↔Modell + 6103-lokal + Diagnose + .nxd +
Reload): **Delete + Rename + Add** über die nicht-sicheren PROFINET-Layouts.

**GUI-Stand:** PROFINET hat **volle Parität** — Add / Delete / Rename / **Reorder** /
**Resize** (`structural=reorderable=resizable=True`). Delete ohne Repack; Add via
`pn_add` (Bit-/Byte-Level, modul-eingeschlossen); **Reorder** = `pn_relocate`
(verschiebt ein Signal in einen freien Slot am/nach dem Drop-Byte, innerhalb eines
Moduls); **Resize** via `pn_skeleton` (Ziel-Modul-Komposition + .nxd) mit
**Fit-Check** (lehnt ein Skeleton ab, dessen Modul-Layout die Signale nicht hält →
„Skeleton aus DIESEM Roboter verwenden"). Gemeinsamer Slot-Finder `_pn_find_slot`.

**Resize-Übersicht (warum nicht überall Spinbox):** PROFINET- und EtherNet/IP-Resize
bleiben **skeleton-basiert** (Dateiauswahl), POWERLINK + EtherCAT nutzen eine reine
**Byte-Spinbox** (`ResizeForm`). Grund: die Bildgröße ist nur bei POWERLINK (@324/@326)
und EtherCAT (@380/@408) ein **einzelnes patchbares .nxd-Feld**. PROFINET-Größe =
Summe der Slot-Module (kein Feld, Resize = Module tauschen). EtherNet/IP-Größe liegt
**nicht** an einem geräteunabhängigen Offset (@1141/@1181 lesen bei echten Roboter-
.nxd 0/Müll) + CIP-Assembly im Blob → Skeleton nötig. (Der `configMD5` wäre
generierbar — er ist = `md5(nxd[136:])`, die Framework-MD5 — aber die Größe selbst
ist es nicht.)

**PROFIsafe (Stufe C) — gelöst & freigeschaltet.** Befund: die sichere Variante
hat **KEINE F-Module/F-Signale im SyCon-Prozessabbild** — nur Standard-Module
(8/16/32 Byte). Die PROFIsafe-Sicherheit liegt komplett in **separaten Dateien**
(`usr/configs/safety.pmt2`, `safetyStruct.json`), die der Writer **nie schreibt**
(`generic_write` schreibt nur sycon/val3/nxd). Editieren des Standard-Prozess-
abbilds lässt die Safety-Dateien **byte-unverändert** (verifiziert: Add+Delete auf
einer safe-Variante, md5 der Safety-Dateien identisch). ⇒ Safe PROFINET-Varianten
sind genauso editierbar wie nicht-sichere; keine F-Slot-Sonderbehandlung nötig.
EtherCAT+FSoE gilt analog (EXAMPLE `_safe` war das validierte Testprojekt).
