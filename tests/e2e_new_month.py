"""Run once explicitly: uv run python tests/e2e_new_month.py.

Uses port 8771 and temporary January–July history; leaves the live service alone.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
PORT = 8771
FILES = {
    "config": "config.xlsx",
    "landing": "DB Landing for FSNE.xlsx",
    "secondary": "Mode_Sec_Month on Month.xlsb",
    "hierarchy": "PH_FG.xlsx",
    "shelflife": "Product Shelflife Report Multiple FSNE  (03-08-2026)  (03-09-2026).csv",
}


def request(method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=300)
    try:
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        content = response.read()
        if response.status >= 400:
            raise AssertionError(f"{method} {path}: {response.status}: {content[:1000]!r}")
        return content
    finally:
        conn.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fsne-e2e-", dir="/tmp") as directory:
        temp = Path(directory)
        history = temp / "history"
        history.mkdir()
        for path in (ROOT / "history").glob("2026_0[2-8]_*.xlsx"):
            shutil.copy(path, history / path.name)
        assert len(list(history.glob("*.xlsx"))) == 7
        env = {**os.environ, "FSNE_RUNS_DIR": str(temp / "runs"), "FSNE_HISTORY_DIR": str(history)}
        with (temp / "server.log").open("w+") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "server", "--port", str(PORT)],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    if proc.poll() is not None:
                        raise AssertionError("Server exited before it was ready")
                    try:
                        request("GET", "/api/slots")
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise AssertionError("Server did not start") from None
                        time.sleep(0.2)
                run = json.loads(request("POST", "/api/runs", b'{"label":"New month e2e"}', {"Content-Type": "application/json"}))
                run_id = run["id"]
                for slot, filename in FILES.items():
                    path = ROOT / "sample_input" / filename
                    request("PUT", f"/api/runs/{run_id}/files/{slot}", path.read_bytes(), {"X-Filename": quote(filename)})
                    print(f"Uploaded {slot}", flush=True)
                request("POST", f"/api/runs/{run_id}/start", b"{}", {"Content-Type": "application/json"})
                deadline = time.monotonic() + 600
                while True:
                    meta = json.loads(request("GET", f"/api/runs/{run_id}"))
                    if meta["status"] not in {"queued", "running"}:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError(f"Timed out: {meta}")
                    time.sleep(2)
                if meta["status"] != "ok":
                    raw = request("GET", f"/api/runs/{run_id}/log").decode(errors="replace")
                    raise AssertionError(f"Run failed: {meta}\n{raw[-6000:]}")
                summary = meta["summary"]
                assert summary["mitigation_carried_batches"] > 0
                assert summary["batches"] > 0 and summary["total_closing_stock_bdt"] > 0
                page = request("GET", f"/?run={run_id}")
                assert b"reportIdentity" in page and b"Monthly upload" in page
                run_dir = temp / "runs" / run_id
                assert (run_dir / "data.json").is_file() and not (run_dir / "index.html").exists()
                actual = json.loads((run_dir / "output" / "current_measures.json").read_text())
                expected = json.loads((ROOT / "output" / "current_measures.json").read_text())
                assert actual == expected, "Sample measures differ from the baseline"
                print(f"E2E PASSED {run_id}: {summary['batches']} batches, "
                      f"{summary['mitigation_carried_batches']} carried, "
                      f"{summary['mitigation_unlabelled_batches']} unlabelled", flush=True)
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()


if __name__ == "__main__":
    main()
