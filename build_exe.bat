@echo off
REM Build the standalone Windows executable (no Python needed to RUN it).
REM Run this once on a machine that HAS Python + the build deps.
cd /d "%~dp0"

echo === Installing build dependencies ===
python -m pip install --upgrade pyinstaller PySide6-Essentials

echo.
echo === Building FieldbusConfigEditorForStaubliCS9.exe ===
python -m PyInstaller --noconfirm --clean FieldbusConfigEditorForStaubliCS9.spec

echo.
echo Done.  ->  dist\FieldbusConfigEditorForStaubliCS9.exe
echo Copy that single .exe anywhere (it creates settings.json + templates\ next to itself).
pause
