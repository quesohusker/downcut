import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "kerfline" / "__init__.py").read_text(encoding="utf-8")).group(1)
ICON = str(ROOT / "packaging" / ("kerfline.ico" if sys.platform.startswith("win") else "kerfline.png"))

datas = [
    (str(ROOT / "kerfline" / "web"), "kerfline/web"),
    (str(ROOT / "kerfline" / "tooldb.json"), "kerfline"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]
datas += collect_data_files("ezdxf")

a = Analysis(
    [str(ROOT / "packaging" / "kerfline_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=collect_submodules("kerfline") + collect_submodules("uvicorn"),
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PySide6"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kerfline",
    console=sys.platform != "darwin",
    argv_emulation=False,
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Kerfline")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Kerfline.app",
        icon=ICON,
        bundle_identifier="com.quesohusker.kerfline",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
