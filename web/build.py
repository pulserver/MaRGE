"""Assemble MaRGE's browser build in ``web/dist``.

The build is the PyQt6 build of Pyodide, the packages of the Pyodide release
it was built from that MaRGE imports, the pure-Python wheels of MaRGE's other
dependencies, and MaRGE itself. Serve ``web/dist`` over HTTP and open
``index.html?console=ws://HOST:PORT`` with ``pulserver console`` listening
there.

Usage: ``python web/build.py [--pyodide DIR]``, where ``DIR`` holds an
unpacked Pyodide release to take the packages from instead of its CDN.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: The PyQt6 build of Pyodide 0.29.3 (Qt 6.10.2, PyQt6 6.10.2; GPL v3).
QT_RUNTIME = (
    "https://github.com/JarrettSJohnson/pyodide-with-pyqt6/releases/download/"
    "v0.29.3.0/pyodide-qt-0.29.3.0.zip"
)
#: The release whose packages the PyQt6 build loads: same ABI, Python and Emscripten.
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/"
RUNTIME_FILES = (
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "pyodide.mjs",
    "pyodide.js",
    "python_stdlib.zip",
    "package.json",
)
#: Pyodide packages MaRGE imports; their dependencies come with them.
PACKAGES = (
    "numpy",
    "scipy",
    "matplotlib",
    "scikit-image",
    "scikit-learn",
    "h5py",
    "pillow",
    "imageio",
    "msgpack",
    "requests",
    "packaging",
    "typing-extensions",
)
#: MaRGE's other dependencies, at the versions of its lock file, as pure-Python wheels.
WHEELS = (
    "pyqtgraph==0.14.0",
    "colorama==0.4.6",
    "QtPy==2.4.3",
    "QDarkStyle==3.2.3",
    "pydicom==3.0.1",
    "nibabel==5.4.2",
    "phantominator==0.7.0",
    "reportlab==4.4.10",
    "xsdata==26.2",
    "ismrmrd==1.14.2",
    "mrd-python==2.2.0",
    "pypulseq==1.4.2",
    "marga-pulseq==0.2.2",
)
#: Parts of the ``marge`` package a browser tab has no use for.
LEFT_OUT = ("docs", "marcos/marcos_server", "marcos/marcos_extras", "MaRGE.egg-info")


def fetch(url: str, cache: Path) -> Path:
    """Return a local copy of ``url``, downloaded into ``cache`` once."""
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response, open(path.with_suffix(".part"), "wb") as out:
            shutil.copyfileobj(response, out)
        path.with_suffix(".part").rename(path)
    return path


def runtime(dist: Path, cache: Path) -> dict:
    """Unpack the PyQt6 build of Pyodide into ``dist``; return its lock file's ``info``."""
    with zipfile.ZipFile(fetch(QT_RUNTIME, cache)) as archive:
        members = {Path(name).name: name for name in archive.namelist()}
        for name in RUNTIME_FILES:
            (dist / name).write_bytes(archive.read(members[name]))
        return json.loads(archive.read(members["pyodide-lock.json"]))["info"]


def packages(dist: Path, cache: Path, source: str, info: dict) -> list[str]:
    """Copy ``PACKAGES`` and their dependencies from ``source`` into ``dist``, with a lock file of them."""

    def read(name: str) -> bytes:
        if source.startswith(("http://", "https://")):
            return fetch(source + name, cache / "pyodide").read_bytes()
        return (Path(source) / name).read_bytes()

    lock = json.loads(read("pyodide-lock.json"))
    if lock["info"]["abi_version"] != info["abi_version"]:
        raise SystemExit(f"{source} is not built for the ABI of the PyQt6 build, {info['abi_version']}")
    wanted, pending = {}, list(PACKAGES)
    while pending:
        name = re.sub(r"[-_.]+", "-", pending.pop()).lower()
        if name not in wanted:
            wanted[name] = lock["packages"][name]
            pending.extend(wanted[name]["depends"])
    for entry in wanted.values():
        (dist / entry["file_name"]).write_bytes(read(entry["file_name"]))
    (dist / "pyodide-lock.json").write_text(json.dumps({"info": info, "packages": wanted}))
    return sorted(wanted)


def wheels(dist: Path) -> list[str]:
    """Download ``WHEELS`` into ``dist``; return their file names."""
    dist.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--quiet", "--no-deps",
            "--only-binary=:all:", "--platform", "any", "--python-version", "3.13",
            "--dest", str(dist), *WHEELS,
        ],
        check=True,
    )
    return sorted(path.name for path in dist.glob("*.whl"))


def marge(dist: Path) -> str:
    """Zip the ``marge`` package into ``dist``; return the archive's name."""
    package = ROOT / "marge"
    left_out = [package / part for part in LEFT_OUT]
    with zipfile.ZipFile(dist / "marge.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if (
                path.is_dir()
                or "__pycache__" in path.parts
                or path.suffix in (".pyc", ".pdf")
                or any(path.is_relative_to(part) for part in left_out)
            ):
                continue
            archive.write(path, path.relative_to(ROOT).as_posix())
    return "marge.zip"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pyodide", default=PYODIDE, help="an unpacked Pyodide release, or its URL")
    parser.add_argument("--dist", type=Path, default=HERE / "dist")
    parser.add_argument("--cache", type=Path, default=HERE / ".cache")
    args = parser.parse_args(argv)

    shutil.rmtree(args.dist, ignore_errors=True)
    (args.dist / "pyodide").mkdir(parents=True)
    info = runtime(args.dist / "pyodide", args.cache)
    manifest = {
        "packages": packages(args.dist / "pyodide", args.cache, str(args.pyodide), info),
        "wheels": wheels(args.dist / "wheels"),
        "marge": marge(args.dist),
    }
    (args.dist / "manifest.json").write_text(json.dumps(manifest, indent=1))
    shutil.copy2(HERE / "index.html", args.dist / "index.html")
    size = sum(path.stat().st_size for path in args.dist.rglob("*") if path.is_file())
    counts = f"{len(manifest['packages'])} packages, {len(manifest['wheels'])} wheels"
    print(f"{args.dist}: {counts}, {size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
