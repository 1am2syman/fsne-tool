"""Launch FSNE from a repository checkout using the bundled runtime."""

from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = ROOT / "runtime"
PACKAGES_DIR = ROOT / ".fsne-packages"
LOCK_FILE = ROOT / "uv.lock"
STAMP_NAME = ".deps-stamp"


def _deps_fingerprint(lock_file: Path, python_version: str) -> str:
    digest = hashlib.sha256()
    digest.update(python_version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(lock_file.read_bytes())
    return digest.hexdigest()


def ensure_packages(
    lock_file: Path = LOCK_FILE,
    packages_dir: Path = PACKAGES_DIR,
    *,
    python_version: str | None = None,
    python_platform: str = "x86_64-pc-windows-msvc",
) -> Path:
    """Install locked dependencies from uv.lock into .fsne-packages via uv."""
    if not lock_file.is_file():
        raise RuntimeError(f"Lock file is missing: {lock_file}")
    py_ver = python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    fingerprint = _deps_fingerprint(lock_file, py_ver)
    stamp = packages_dir / STAMP_NAME
    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == fingerprint:
        return packages_dir

    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required to install FSNE dependencies. "
            "Install it from https://docs.astral.sh/uv/getting-started/installation/"
        )

    print("Preparing dependencies with uv...", flush=True)
    packages_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".fsne-packages-new-", dir=packages_dir.parent))
    req_dir = Path(tempfile.mkdtemp(prefix=".fsne-req-", dir=packages_dir.parent))
    backup: Path | None = None
    env = {**os.environ, "UV_PYTHON_DOWNLOADS": "never"}
    try:
        req_file = req_dir / "requirements.txt"
        export = subprocess.run(
            [
                uv, "export", "--frozen", "--no-emit-project", "--no-dev",
                "--no-header", "--no-annotate", "--output-file", str(req_file),
            ],
            check=False,
            cwd=lock_file.parent,
            env=env,
        )
        if export.returncode != 0:
            raise RuntimeError("uv export failed. Check the messages above.")

        install = subprocess.run(
            [
                uv, "pip", "install",
                "--target", str(staging),
                "--python-version", py_ver,
                "--python-platform", python_platform,
                "--no-python-downloads",
                "--require-hashes",
                "--only-binary", ":all:",
                "-r", str(req_file),
            ],
            check=False,
            cwd=lock_file.parent,
            env=env,
        )
        if install.returncode != 0:
            raise RuntimeError("uv pip install failed. Check the messages above.")

        (staging / STAMP_NAME).write_text(fingerprint + "\n", encoding="utf-8")
        if packages_dir.exists():
            backup = Path(tempfile.mkdtemp(prefix=".fsne-packages-old-", dir=packages_dir.parent))
            backup.rmdir()
            packages_dir.rename(backup)
        staging.rename(packages_dir)
    except BaseException:
        if backup is not None and backup.exists() and not packages_dir.exists():
            backup.rename(packages_dir)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if req_dir.exists():
            shutil.rmtree(req_dir, ignore_errors=True)
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    return packages_dir


def _using_bundled_runtime() -> bool:
    try:
        return Path(sys.executable).resolve().is_relative_to(RUNTIME_DIR.resolve())
    except OSError:
        return False


def main() -> None:
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    if _using_bundled_runtime():
        packages = ensure_packages()
        packages_str = str(packages)
        if packages_str not in sys.path:
            sys.path.insert(0, packages_str)
        # Clear cached missing-directory finders created before .fsne-packages existed.
        sys.path_importer_cache.clear()
        importlib.invalidate_caches()

    from server.__main__ import main as server_main

    server_main()


if __name__ == "__main__":
    main()
