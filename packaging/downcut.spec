import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "downcut" / "__init__.py").read_text(encoding="utf-8")).group(1)
ICON = str(ROOT / "packaging" / ("downcut.ico" if sys.platform.startswith("win") else "downcut.png"))

datas = [
    (str(ROOT / "downcut" / "web"), "downcut/web"),
    (str(ROOT / "downcut" / "tooldb.json"), "downcut"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]
datas += collect_data_files("ezdxf")

a = Analysis(
    [str(ROOT / "packaging" / "downcut_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=collect_submodules("downcut") + collect_submodules("uvicorn"),
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PySide6"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Downcut",
    console=sys.platform != "darwin",
    argv_emulation=False,
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Downcut")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Downcut.app",
        icon=ICON,
        bundle_identifier="com.quesohusker.downcut",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
