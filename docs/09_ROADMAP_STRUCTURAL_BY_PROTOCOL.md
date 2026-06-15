# 09 — Roadmap: Strukturedit für EtherNet/IP, PROFINET, PROFINET+PROFIsafe

Ziel: dieselben Funktionen wie für EtherCAT (Add / Delete / Reorder / Resize),
**byte-exakt und in SyCon.net validiert**, auch für die übrigen bit-granularen
Protokolle. Methodik und Fallstricke: siehe [`08_STRUCTURAL_EDITING.md`](08_STRUCTURAL_EDITING.md).

Begriffsklärung: **FSoE** = Safety over EtherCAT (bereits abgedeckt, s. u.).
**PROFIsafe** = Safety bei PROFINET. „PROFINET mit Safety" meint hier PROFIsafe.

Gemeinsames **Abnahmekriterium** für jede Stufe: der *harte Check* (Doc 08 §4,
Schritte 1–5) über **alle** ladbaren Projekte des Protokolls **plus** mindestens
eine **SyCon.net-Gegenprobe** je Variante.

---

## A. EtherNet/IP — „fast fertig", nur zertifizieren  (Aufwand: klein, Risiko: niedrig)

Strukturedit funktioniert bereits (1 Connect-Modul „Slot 1", kein Grenzproblem;
Val3-Regenerierung generalisiert & gegengeprüft; `.nxd`-Größe @1141/@1181 in
**Bits**; Resize per Skeleton inkl. `configMD5`).

1. **Harten Sweep nachziehen** (wie EtherCAT 36/36): über alle EtherNet/IP-Projekte
   Add / Delete / Reorder / Resize → Modulstruktur (1 Modul), Val3↔Modell-UIDs,
   6103-Offsets, `.nxd`. Heute nur per Selbstcheck abgedeckt → unzureichend.
2. **Beide Varianten testen:** per-Bit (`ae=1`) **und** byte-packed (`ae=8`,
   accessPath „0" statt „0.0"). `EipAddForm` klont `ae` aus dem Bestand — über
   beide Varianten verifizieren.
3. **Resize hart prüfen:** Skeleton-`.nxd`-Größe (@1141/@1181) = Ziel, `configMD5`
   nur größenabhängig → Cross-Size-Test (z. B. 64↔104 B).
4. **SyCon-Gegenprobe** einer generierten EtherNet/IP-Datei.

**Definition of Done:** harter Sweep grün über alle EIP-Projekte + 1 SyCon-OK.

---

## B. PROFINET (ohne Safety) — Neuentwicklung  (Aufwand: groß, Risiko: hoch)

Heute rename + save only.

### B1. Reverse Engineering — **ERLEDIGT** (Details: Doc 08 §5)
Frühere „per-Slot-restart"-Annahme war falsch. Fakten:
- Verschachtelte **Slot/Subslot-Module aus Größen-Katalog** („N Byte Eingang/
  Ausgang"); Summe der Modulgrößen je Richtung = Prozessabbild-Größe; **kein**
  Einzel-Größenfeld in `.nxd`/`_nwid.nxd`.
- Modulrichtung **invertiert benannt** → `signalType` ist maßgeblich.
- **accessPath = globaler Byte-Offset** je Richtung (durchgehend), **kein**
  per-Slot-Restart.
- Format: **bit→`byte.bit` immer**, sonst `byte`. ⇐ einziger Renderunterschied.

### B2. Writer (Verallgemeinerung des EtherCAT-Codes)
- **`_access_path` protokollabhängig:** für PROFINET `dataType=="bit"` → `byte.bit`
  (sonst `byte`). Heutige Logik (bit + ae%8 → byte.bit) deckt das NICHT ab.
- **`_build_detail`: n-Modul-Platzierung.** Skeleton-Modulstruktur (Reihenfolge +
  Subslot-Schachtelung) übernehmen; je (Sub)Modul die Signale der passenden
  Richtung einsetzen, deren globaler Offset in den Byte-Bereich des Moduls fällt.
  Modul-Byte-Bereiche aus den Modulgrößen je Richtung akkumulieren. Leere Module
  bleiben leer (Richtung dann aus invertiertem Modulnamen).
- **`write_val3`:** schon pro-Modul generalisiert — Byte-Bereich-Platzierung +
  Subslot-Schachtelung ergänzen, Außen-/F-Signale unberührt.
- **`.nxd`:** Add/Delete/Reorder → verbatim. **Resize** = andere Slot-Komposition →
  nur per **Skeleton** (PROFINET-Projekt der Zielgröße); GSDML-Katalog-Generator
  als spätere Option.

### B3. Modell — **modul-bewusst, GLOBALE Offsets** (Pflicht, s. Doc 08 §5b)
- **Heute speichert `parse_signals` den LOKALEN 6103-Offset** (je Modul 0-basiert)
  → globale Position unbekannt, mehrere Signale mit `bit_offset=0`. Muss auf den
  **globalen** Offset umgestellt werden (aus `accessPath`, bzw. Modulstart + lokal).
- **Kein globales `repack_bits`**: PROFINET-Signale sitzen modul-ausgerichtet mit
  Lücken. Add = freies Byte in einem Modul benennen; Delete = Byte freigeben;
  Offsets der anderen bleiben. Repack nur modul-lokal (falls überhaupt).
- Beim Schreiben: `accessPath = global`, `6103 = global − Modulstart`. Modul-Byte-
  Bereiche je Richtung aus dem Skeleton (Summe der „N Byte"-Modulgrößen).
- `_render_by_modules` + PROFINET-Zweig in `_build_detail` sind gebaut (dormant),
  brauchen aber dieses globale Modell, um korrekt zu platzieren.

### B4. Validierung
- Harter Check **n-Modul-tauglich**: jedes Modul enthält nur Signale seiner
  Richtung mit Offsets im Modul-Bereich; Val3↔Modell-UIDs je Modul; accessPath/
  6103-Offsets; `.nxd`. Sweep über alle PROFINET-Projekte, dann SyCon-Gegenprobe.

**Schrittweise freischalten:** erst Reorder/Rename, dann Add/Delete (innerhalb der
festen Slot-Belegung), zuletzt Slot-Resize per Skeleton.

---

## C. PROFINET + PROFIsafe  (Aufwand: groß+, Risiko: sehr hoch — sicherheitsrelevant)

Baut auf B auf. Die sichere Variante hat zusätzliche **F-Module/Slots** mit
PROFIsafe-Signalen. Sichere Konfigurationen sind **roboterspezifisch und nicht
generierbar** (s. `safety.py` / Memory) — daher gilt:

1. **F-Slots erkennen und als UNVERÄNDERLICH behandeln.** Strukturedit nur auf den
   **Standard-Prozessdaten-Slots**; Safety-Bytes/-Signale nie anfassen oder
   verschieben.
2. Validierung zusätzlich: Safety-Mapping vor/nach identisch (Byte-Diff der
   F-Bereiche = 0), `safety.pmt2`/`safetyStruct.json` unberührt.
3. **EtherCAT + FSoE** ist bereits abgedeckt: Testprojekt EXAMPLE war die `_safe`-
   Variante — Add/Delete/Reorder/Resize liefen byte-exakt. Vor Freigabe trotzdem
   prüfen, dass die FSoE-PDO-Signale nicht umgeordnet werden (sie sind reguläre
   Signale in den PDOs); ggf. F-Signale ebenfalls als fix markieren. **Hier ist die
   SyCon- + Safety-Toolchain-Gegenprobe zwingend.**

---

## Reihenfolge & Empfehlung
1. **A (EtherNet/IP zertifizieren)** — schnell, schließt eine Lücke gleicher Art
   wie bei EtherCAT (Selbstcheck war auch hier die einzige Absicherung).
2. **B1 (PROFINET-RE)** — ohne die Slot-/`.nxd`-Kartierung geht nichts; read-only,
   gut parallelisierbar.
3. **B2–B4 (PROFINET-Implementierung)** schrittweise, jeweils mit hartem Check.
4. **C (PROFIsafe / FSoE-Absicherung)** zuletzt, mit verpflichtender SyCon- und
   Safety-Gegenprobe.

Jede Stufe endet mit: harter Sweep grün + SyCon-OK → erst dann im GUI-Gating
(`raw['structural']`/`raw['resizable']`) freischalten.
