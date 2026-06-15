# Third-party components

This application bundles / uses the following components under their own licences:

## Qt — via PySide6  (GNU LGPL v3)
- Qt: https://www.qt.io — © The Qt Company Ltd. and contributors.
- PySide6 (Qt for Python): https://wiki.qt.io/Qt_for_Python

This program uses the Qt libraries under the **LGPL v3**. As required by the LGPL:
- the Qt source is available from https://download.qt.io ;
- you may run this program with a modified, compatible version of Qt. The full
  source of this application is published (GPL v3), so the distributed binaries
  can be rebuilt and the Qt libraries replaced.

> Tip for distribution: a PyInstaller **one-folder** build (instead of one-file)
> keeps the Qt DLLs as separate, replaceable files, which makes LGPL relinking
> obviously possible. The one-file .exe is acceptable because the full source is
> provided.

## Other
- Python — Python Software Foundation License.
- PyInstaller — used to build the .exe (GPL with a bundling exception; the
  generated executable is not encumbered).
- Pillow — used only to generate the icon (HPND, permissive).
