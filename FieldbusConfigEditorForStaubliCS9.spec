# PyInstaller spec — builds a standalone Windows .exe (no Python needed).
# Build with:  python -m PyInstaller --noconfirm FieldbusConfigEditorForStaubliCS9.spec
# Output:      dist/FieldbusConfigEditorForStaubliCS9.exe   (single file, windowed)
#
# settings.json and templates/ are created NEXT TO the .exe at runtime (portable).
block_cipher = None

a = Analysis(
    ['run_gui.py'],
    pathex=['src'],                 # so gui.* and fbconfig.* resolve
    binaries=[],
    datas=[
        ('assets/inasoft_strokes.png', 'assets'),
        ('assets/inasoft_icon.png', 'assets'),
        ('assets/inasoft_logo.png', 'assets'),
        ('assets/gsdml/GSDML-V2.33-HILSCHER-NETX 51-RE PNS-20161212.xml', 'assets/gsdml'),
        ('assets/templates/powerlink.zip', 'assets/templates'),
        ('assets/templates/ethernetip.zip', 'assets/templates'),
        ('assets/templates/ethercat.zip', 'assets/templates'),
        ('assets/templates/profinet.zip', 'assets/templates'),
    ],
    hiddenimports=[
        'fbconfig.protocols.powerlink',
        'fbconfig.protocols.ethernetip',
        'fbconfig.protocols.ethercat',
        'fbconfig.protocols.profinet',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[                       # GUI doesn't use these -> smaller exe
        'tkinter', 'cli', 'unittest', 'pydoc', 'test',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtNetwork',
        'PySide6.QtWebEngineCore', 'PySide6.Qt3DCore',
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='FieldbusConfigEditorForStaubliCS9',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,                   # windowed GUI (no console window)
    disable_windowed_traceback=False,
    icon='assets/inasoft_icon.ico',  # taskbar / exe icon (one stroke)
)
