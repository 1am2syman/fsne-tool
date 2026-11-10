"""Disposable live app for browser regressions; never opens the real run store."""
import json
import signal
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.app import make_server
from server.runs import Store
from tests.test_run_export_regression import history, seed
from ui.render import extract_legacy

# Let TemporaryDirectory clean up the report fixtures when the browser stops us.
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

with tempfile.TemporaryDirectory(prefix="fsne-upload-browser-") as directory:
    store = Store(Path(directory) / "runs", Path(directory) / "history", start_worker=False)
    payload = json.dumps(extract_legacy(Path("ui/index.html").read_text()))
    for archived in (False, True):
        ident, folder = seed(store)
        (folder / "data.json").write_text(payload)
        history(folder, "saved-0000.xlsx", "2026-07-31", ["exact-1-2"], ["July plan"])
        store.update(ident, archived="2026-11-10" if archived else None, rows=3, run_date="2026-11-10")
    server = make_server(store, port=0)
    print(json.dumps({"port": server.server_port, "directory": directory}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
