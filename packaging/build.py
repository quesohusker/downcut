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
        return "Downcut-macOS-AppleSilicon.zip" if platform.machine() == "arm64" else "Downcut-macOS-Intel.zip"
    if sys.platform.startswith("win"):
        return "Downcut-Windows.zip"
    return "Downcut-Linux.tar.gz"


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
            str(ROOT / "packaging" / "downcut.spec"),
        ],
        check=True,
    )


def package():
    target = DIST / asset_name()
    if sys.platform == "darwin":
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(DIST / "Downcut.app"), str(target)],
            check=True,
        )
    elif sys.platform.startswith("win"):
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in (DIST / "Downcut").rglob("*"):
                archive.write(path, Path("Downcut") / path.relative_to(DIST / "Downcut"))
    else:
        with tarfile.open(target, "w:gz") as archive:
            archive.add(DIST / "Downcut", arcname="Downcut")
    print(target, round(target.stat().st_size / 1024 / 1024, 1), "MB")
    return target


def executable():
    if sys.platform == "darwin":
        return DIST / "Downcut.app" / "Contents" / "MacOS" / "Downcut"
    if sys.platform.startswith("win"):
        return DIST / "Downcut" / "Downcut.exe"
    return DIST / "Downcut" / "Downcut"


if __name__ == "__main__":
    if "--package-only" not in sys.argv:
        build()
    package()
    print("executable:", executable())
