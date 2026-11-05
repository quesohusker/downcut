import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "packaging" / "dist"


def asset_name():
    if sys.platform == "darwin":
        return "Kerfline-macOS-AppleSilicon.zip" if platform.machine() == "arm64" else "Kerfline-macOS-Intel.zip"
    if sys.platform.startswith("win"):
        return "Kerfline-Windows.zip"
    return "Kerfline-Linux.tar.gz"


def build():
    subprocess.run([sys.executable, str(ROOT / "packaging" / "third_party.py")], check=True)
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(ROOT / "packaging" / "build", ignore_errors=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(ROOT / "packaging" / "build"),
            str(ROOT / "packaging" / "kerfline.spec"),
        ],
        check=True,
    )


def package():
    target = DIST / asset_name()
    if sys.platform == "darwin":
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(DIST / "Kerfline.app"), str(target)],
            check=True,
        )
    elif sys.platform.startswith("win"):
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in (DIST / "Kerfline").rglob("*"):
                archive.write(path, Path("Kerfline") / path.relative_to(DIST / "Kerfline"))
    else:
        with tarfile.open(target, "w:gz") as archive:
            archive.add(DIST / "Kerfline", arcname="Kerfline")
    print(target, round(target.stat().st_size / 1024 / 1024, 1), "MB")
    return target


def executable():
    if sys.platform == "darwin":
        return DIST / "Kerfline.app" / "Contents" / "MacOS" / "Kerfline"
    if sys.platform.startswith("win"):
        return DIST / "Kerfline" / "Kerfline.exe"
    return DIST / "Kerfline" / "Kerfline"


if __name__ == "__main__":
    if "--package-only" not in sys.argv:
        build()
    package()
    print("executable:", executable())
