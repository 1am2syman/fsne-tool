# Bundled Windows Python runtime

`windows-x64/` contains the **CPython 3.14.10 Windows x64 embeddable distribution** from the Python Software Foundation, with `python314._pth` configured to keep the interpreter isolated while adding the repository root (`..\..`) and installed packages (`..\..\.fsne-packages`) to `sys.path` for both `start.py` and pipeline subprocesses.

Third-party packages (`polars`, `fastexcel`, `openpyxl`, `xlsxwriter`) are **not** vendored in Git. On first launch (or whenever `uv.lock` changes after `update.bat`), `start.py` uses the user's installed `uv` (with `UV_PYTHON_DOWNLOADS=never`) to install the hash-verified dependencies from `uv.lock` into the ignored `.fsne-packages/` directory. Subsequent launches skip `uv` and start offline.

- Supported launcher target: **64-bit Windows 10/11 (Intel/AMD x64)**. Not native ARM64 or 32-bit.
- Upstream release: <https://www.python.org/downloads/release/python-31410/>
- Original archive: <https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip>
- Archive SHA-256: `90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159`
- License and bundled notices: [windows-x64/LICENSE.txt](windows-x64/LICENSE.txt)
- Upstream SBOM: [python-3.14.10-embed-amd64.spdx.json](python-3.14.10-embed-amd64.spdx.json)
- Extracted runtime checksums: [windows-x64.manifest.json](windows-x64.manifest.json)

## Verify

```bash
uv run python -m unittest discover -s tests -p "test_runtime.py" -v
```

This checks every extracted runtime file hash, Windows x64 PE headers, standard-library ZIP integrity, isolation settings in `python314._pth`, and SBOM provenance. On Windows it also runs `runtime\windows-x64\python.exe -B start.py --help`, verifies dependency imports from `.fsne-packages/`, and exercises child subprocesses (`-m src --help` and `ui/build.py --help`).
