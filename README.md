# Fieldbus Config Editor for Stäubli CS9

Werkzeug zum **Erstellen und Bearbeiten** von Hilscher-netX-Fieldbus-Konfigurationen
(SyCon.net-Projekte) für Stäubli-CS9-Roboter — **ohne SyCon.net**.

Entwickelt von **[inasoft GmbH](https://www.inasoft.ch)** — Ihr kompetenter Partner
für die Programmierung von Stäubli-Robotern.

> **Stäubli** is a trademark of Stäubli International AG. This project is
> **independent** and **not** affiliated with, endorsed by or sponsored by
> Stäubli — the name only describes the robots it is built for.

## Lizenz
- **Code:** GNU **GPL v3** (siehe [`LICENSE`](LICENSE)) — Open Source mit Copyleft:
  jede Weitergabe/Änderung muss ebenfalls quelloffen unter GPL v3 bleiben. Eine
  Einbindung in **proprietäre** Software (auch durch Dritte) ist damit **nicht**
  erlaubt.
- **Marken:** „inasoft“ und das inasoft-Logo sind **Marken** der inasoft GmbH —
  die GPL gewährt **keine** Markenrechte (siehe [`TRADEMARKS.md`](TRADEMARKS.md)).
  Forks müssen das inasoft-Branding ersetzen, die Urheber-/Attributionshinweise
  aber behalten.
- **Drittkomponenten:** Qt/PySide6 unter LGPL v3 (siehe
  [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)).
- **Keine Gewähr / keine Haftung** (GPL v3 §15–17). Erzeugte Konfigurationen
  **vor dem Aufspielen in SyCon.net prüfen** — die Software arbeitet an
  sicherheitsrelevanten Roboter-Konfigurationen.

## Dokumentation (alles Wissen liegt im Projekt)
- `docs/ARCHITECTURE.md` — Architektur, Modul-Übersicht, Datenfluss, Erweiterung (Start hier).
- `docs/STRUCTURE.md` — Format SYCON_net.xml (Blob, Records, Längenfeld, OLE2).
- `docs/06_NXD_FORMAT.md` — Format .nxd (Header, MD5, Feld-Offsets).
- `docs/07_VAL3_XML_FORMAT.md` — Format J207J208.xml (Val3-Symbole).
- `docs/08_STRUCTURAL_EDITING.md` — Strukturedit (Add/Delete/Reorder/Resize): .nxd-
  Größenfelder pro Protokoll, RxPDO/TxPDO-Modulgrenze, harter Validierungs-Check.
- `docs/09_ROADMAP_STRUCTURAL_BY_PROTOCOL.md` — Plan: Strukturedit für EtherNet/IP,
  PROFINET und PROFINET+PROFIsafe implementieren & validieren.
- `docs/10_SYCON_TEST_PLAN.md` — priorisierter Edge-Case-Plan für die SyCon.net-
  Gegenprobe (das eigentliche Korrektheits-Tor).
- `docs/REPORT.md` — Gesamtbericht des Reverse Engineering (byte-validiert).

## Ziel
- Stäubli-Ordner auswählen → Programm arbeitet in diesem Ordner.
- Bestehende Konfiguration einlesen und bearbeiten (Bytezahl, bool/word/real-Belegung,
  Node-ID, Parameter) **ohne Verlust der Belegung**.
- Neue Konfigurationen erstellen.
- Alle drei Dateiebenen konsistent schreiben:
  `SYCON_net.xml` (DTM/Belegung) + `J207J208.xml` (Val3-Symbole) + `J207J208.nxd` (Chip).
- **Mehrere Busprotokolle** (POWERLINK funktioniert; PROFINET/EtherCAT/EtherNet-IP/…
  per Plugin, sobald Beispiel-Dateien vorliegen).

## Stand
- POWERLINK CIFX RE/PLS vollständig geknackt & byte-validiert (siehe `docs\`).
- `prototype\` = funktionierende RE-/Generator-Skripte (Basis für die Library).
- `memory\` = kompletter Wissensstand.

### Implementiert (lauffähig)
- `src/fbconfig/` Core (keine UI): `datatypes`, `model`, `naming`, `sycon`, `nxd`, `project`.
- `src/cli/` Konsole: Ordner-Picker (tkinter) → Projekt finden → analysieren → Übersicht.
- Start: `python run.py` (Picker) oder `python run.py <roboter-ordner>`.
- Getestet gegen echte Roboter-Konfig: Protokoll, Node, Name, IP, Vendor/Product,
  .nxd-MD5, Bytes In/Out, Datentyp-Übersicht werden korrekt angezeigt.

- `src/fbconfig/` Edit+Write: `settings`, `writers`, `backup`, `save`; `src/cli/edit.py`.
- Geführtes Edit-Menü `[e]`: add (append/insert, separate/array, alle 11 Typen,
  Count durch freie Bytes begrenzt + Fehler, Auto-Nummerierung mit gemerktem
  Start/Stellen), delete, rename, Größe ändern, Node/IP/Name, preview, **save**.
- `[w] Save`: Backup-ZIP (Datum/Uhrzeit) → schreibt alle 3 Dateien → Round-Trip-
  Selbstprüfung → `changes.log`. Getestet (Sandbox): unverändert + Größe/Node/Append.

- `src/gui/` Desktop-GUI (PySide6, modernes Dark-Theme) — **zweiter Client derselben
  `fbconfig`-Core**, kann alles, was die CLI kann: Ordner öffnen + Projektauswahl,
  Übersichts-Karten (Gerät/Protokoll/Node/IP/.nxd-MD5), In/Out-Signaltabellen mit
  Kapazitätsbalken, Add (separate/array, alle 11 Typen, Byte-Limits, Auto-Nummerierung),
  Delete, Rename (auch Doppelklick), Resize, Node/IP/Name, **Save** (Backup + Write +
  Round-Trip-Prüfung mit Ergebnis-Report).
- Start: `python run_gui.py` oder `python run_gui.py <roboter-ordner>`.
- Voraussetzung: `python -m pip install PySide6-Essentials`.

### Standalone-EXE (ohne Python-Installation)
- Bauen: `build_exe.bat` doppelklicken (oder `python -m PyInstaller --noconfirm FieldbusConfigEditorForStaubliCS9.spec`).
  Voraussetzung nur zum **Bauen**: Python + `pip install pyinstaller PySide6-Essentials`.
- Ergebnis: `dist\FieldbusConfigEditorForStaubliCS9.exe` (~36 MB, eine Datei, kein Python nötig zum **Ausführen**).
- **Portabel**: `settings.json` und `templates\` werden **neben der .exe** angelegt — die .exe in einen
  schreibbaren Ordner kopieren (nicht nach `C:\Program Files`).

### Als Nächstes
- Optional: Preview/Diff vor Save, Alignment-Warnung, „mirror In->Out".
- „Neue Konfiguration" (template-basiert) + weitere Protokolle (brauchen Beispiel-Projekte).
- Später GUI (PySide6) als zweiter Client derselben `fbconfig`-Core.

## Geplante Architektur
```
src/fbconfig/                 # protokoll-agnostische Core-Library (keine GUI)
  model.py                    # ConfigModel: Protocol, Params, Module[], Signal[]
  sycon_store.py              # SYCON_net.xml Blob lesen/schreiben (OLE2, Records,
                              #   Längenfeld, eingebettetes OLE2) — framework-weit
  exporters/
    val3_xml.py               # J207J208.xml erzeugen
    nxd.py                    # J207J208.nxd erzeugen (MD5)
  protocols/
    base.py                   # Protocol-Interface (Param-Set, Modulkatalog, nxd-Offsets)
    powerlink.py              # POWERLINK-Spezifika
    profinet.py  (geplant)    # je weiteres Protokoll ein Plugin
src/gui/                      # GUI-Schicht
samples/                      # Beispiel-Konfigurationen je Protokoll (für Entwicklung)
```

## Multi-Protokoll-Strategie
Das SyCon/netX-**Framework** (OLE2-Container, Hex-Blob, Record-Format, Längenfeld,
.nxd-Header+MD5) ist sehr wahrscheinlich protokollübergreifend gleich → wiederverwendbar.
**Protokoll-spezifisch** sind: Parameter-Sätze, Modulkatalog/Signaltypen, .nxd-Feld-Offsets.
Dafür werden **Beispiel-Konfigurationsdateien pro Protokoll** benötigt (vom Nutzer geliefert).

## Offene Entscheidung
- Programmiersprache: **Python** (empfohlen — Code existiert bereits).
- GUI: Desktop (PySide6/Qt) vs. Konsole (Textual) vs. Web (Streamlit) — siehe Chat.
