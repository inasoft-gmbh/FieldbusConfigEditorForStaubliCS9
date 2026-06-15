# 10 — SyCon.net-Gegenprobe: Edge-Case-Testplan

Der interne Round-Trip-Selbstcheck ist **nicht ausreichend** — er hat in dieser
Entwicklung mehrfach strukturell kaputte Dateien als „verified" durchgewunken
(EtherCAT-Modulgrenze, EtherCAT/PROFINET-Val3, POWERLINK-Einzelbits). **Nur die
SyCon.net-Gegenprobe bestätigt die Korrektheit.**

## Vorgehen je Test
1. Tool macht beim Speichern automatisch ein **ZIP-Backup** (in `usr/`). Trotzdem
   am besten an einer **Kopie** testen.
2. Operation im Tool ausführen → **Save**.
3. Das `.spj` in **SyCon.net öffnen** und prüfen (Checkliste unten).
4. Zusätzlich: das **Val3-Roboterprogramm** (J207J208.xml) prüfen — sieht es die
   richtigen Variablen/Adressen? (Genau hier waren Bugs unsichtbar.)

## Was in SyCon.net IMMER prüfen (Checkliste)
- [ ] Projekt öffnet **ohne Fehler/Warnung**.
- [ ] Prozessabbild: **In/Out-Größe** korrekt (Bytes).
- [ ] **Modulstruktur** vollständig (EtherCAT: RxPDO+TxPDO; PROFINET: alle
      Slots/Subslots; EtherNet/IP: Connect; POWERLINK: In/Out-Modul).
- [ ] **Keine Lücken-/Überlappungs-Warnung** im Mapping (außer gewollte Lücken).
- [ ] Signal-**Namen + Adressen** wie erwartet.
- [ ] **Download/Konsistenz-Check** in SyCon läuft durch (.nxd/MD5 ok).
- [ ] Val3: die geänderten Variablen erscheinen an der richtigen Adresse.

---

## PRIORITÄT 1 — wo Bugs gefunden wurden (zuerst testen!)

| # | Test | Worauf achten |
|---|---|---|
| 1 | **EtherCAT: erstes OUTPUT-Signal löschen** | RxPDO/TxPDO bleiben **2 Module** (war der Modulgrenz-Bug) |
| 2 | **EtherCAT: Output-Signal per Drag verschieben** | Modulgrenze bleibt korrekt |
| 3 | **EtherCAT/PROFINET: Add+Delete, dann Val3 prüfen** | Val3-Signalliste = SyCon (Val3 wurde früher nicht aktualisiert) |
| 4 | **POWERLINK mit Einzelbits speichern** (z. B. EXAMPLE/TX2_60_1) | Bits bleiben `116.0…116.7` (nicht alle auf `.0` kollabiert) |
| 5 | **Delete in der Mitte → Lücke** | SyCon akzeptiert das **unbelegte Byte**; andere Adressen unverändert |
| 6 | **Safe-Variante editieren** (FSoE/PROFIsafe) | `safety.pmt2`/`safetyStruct.json` **unverändert**; Safety-Toolchain ok |

## PRIORITÄT 2 — strukturelle Operationen je Protokoll

**Delete**
- [ ] Erstes / mittleres / **letztes** Signal löschen.
- [ ] Mehrbyte-Signal (word/real32) löschen → Mehrbyte-Lücke.
- [ ] Einzel-Bit löschen (POWERLINK/EtherCAT) → halb belegtes Byte ok?
- [ ] Mehrere auf einmal löschen.
- [ ] Alle Signale einer Richtung löschen (Extremfall).

**Add**
- [ ] In eine **Lücke** (nach Delete) und am **Ende** anfügen.
- [ ] Jeden **Datentyp** einmal (bit, byte, word, dword, real32, signed/unsigned 8/16/32).
- [ ] **Einzel-Bit (ae=1)** vs **byte-packed Bit (ae=8)** wo verfügbar.
- [ ] **Array** hinzufügen.
- [ ] Bei **vollem** Interface: Tool muss **ablehnen** (keine kaputte Datei).
- [ ] PROFINET: Add landet im richtigen **Slot** (kleines 8B- vs großes 64B-Modul).

**Reorder / Drag**
- [ ] Innerhalb In; innerhalb Out.
- [ ] Mehrere Signale gleichzeitig ziehen.
- [ ] Auf eine **Lücke** vs auf ein Signal droppen.
- [ ] PROFINET: Relocate nahe einer Modulgrenze.

**Resize** (Skeleton-basiert bei EtherCAT/EtherNet-IP/PROFINET)
- [ ] **Vergrößern** und **verkleinern**.
- [ ] PROFINET: **inkompatibles** Skeleton → Tool muss **ablehnen** (Fit-Check).
- [ ] POWERLINK: Bytezahl ändern → .nxd-Länge/MD5 in SyCon ok.
- [ ] Nach Resize: Skeleton-Identität (IP/Node) wie erwartet.

## PRIORITÄT 3 — Identität, Varianten, Sonstiges

**General (IP / Node / Station / Name)**
- [ ] EtherNet/IP **IP** ändern (Blob + Val3 `<ip>`).
- [ ] POWERLINK **Node-ID** ändern (.nxd).
- [ ] PROFINET **Gerätename** / EtherCAT **Station** ändern.
- [ ] Auf einen **längeren** Wert ändern (passt er noch ins Blob-Feld? sonst nur Val3).

**Format-Varianten** (beide Schreibwege existieren)
- [ ] EtherNet/IP **per-bit (ae=1)** UND **byte-packed (ae=8)** Projekt.
- [ ] EtherCAT per-bit vs byte-packed.
- [ ] PROFINET: verschiedene Modul-Kompositionen (8/16/32/64B), **asymmetrisch** In≠Out.
- [ ] POWERLINK **mit** und **ohne** Einzelbits.

**Safety**
- [ ] FSoE/PROFIsafe **ON↔OFF schalten** (Switch) → beide Varianten in SyCon ok.
- [ ] Safety-**Template anwenden** → danach IP/Station via General setzen.

**Workflow**
- [ ] **Neue Config** in einen Roboter ohne Feldbus klonen → editieren → SyCon.
- [ ] **Backup-ZIP** zurückspielen stellt das Original wieder her.
- [ ] **Kombi**: Rename + Delete + Add + General in einem Save.

---

## Worst-Case-Kombinationen (wenn Zeit bleibt)
- EtherCAT: Output[0] löschen **+** Input-Signal verschieben **+** Add **+** Save → SyCon.
- PROFINET safe: Add+Delete im Standard-Prozessdaten-Slot, dann **Safety-Tool** prüfen.
- POWERLINK: Einzelbit löschen, anderes Einzelbit dazu, Real verschieben → SyCon + Val3.

> Wenn ein Test in SyCon scheitert: das **ZIP-Backup** stellt das Original her;
> melde mir die genaue Operation + das Protokoll + was SyCon meldet, dann fixe ich
> es gezielt (mit einem harten Check, der genau diesen Fall abdeckt).
