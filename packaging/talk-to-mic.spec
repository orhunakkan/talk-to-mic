# PyInstaller spec: builds dist\talk-to-mic\talk-to-mic.exe (a folder, not a single file, so
# it starts fast and isn't unpacked to %TEMP% on every launch). Run via build.ps1.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=collect_data_files("faster_whisper"),  # Silero VAD model used by vad_filter
    # Only QtCore/QtGui/QtWidgets are used; keep the rest of Qt out of the bundle.
    excludes=["pytest", "tkinter", "pystray", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtPdf", "PySide6.QtSvg", "PySide6.QtOpenGL", "PySide6.QtDBus", "PySide6.QtSql",
              "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtConcurrent", "PySide6.QtPrintSupport"],
)
# Qt Widgets paint without OpenGL, and the UI is English: drop the software OpenGL fallback
# (~20 MB) and Qt's own translations.
a.binaries = [b for b in a.binaries if not b[0].lower().endswith("opengl32sw.dll")]
a.datas = [d for d in a.datas if "PySide6\\translations" not in d[0] and "PySide6/translations" not in d[0]]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="talk-to-mic",
    icon=str(ROOT / "build" / "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="talk-to-mic", upx=False)
