"""HTTP API + dashboard host. Standard library only; bind to localhost and proxy.

Every URL is relative to the page, so the app works under a path prefix such as
``/fsne-app/`` behind ``preview --proxy``.

    GET    /                          dashboard (latest completed run, or ?run=RUN-0001)
    GET    api/runs                   all runs, newest first
    POST   api/runs                   {"label", "archive_previous"} -> new draft run
    PUT    api/runs/<id>/files/<slot> raw body, X-Filename header
    POST   api/runs/<id>/config       {"fsne_month", "label"?} -> month-end configuration (legacy report_date accepted)
    POST   api/runs/<id>/start        queue the run; explicit override_unsettled for a history gap
    GET    api/runs/<id>              one run
    GET    api/runs/<id>/log          pipeline log (text)
    GET    api/runs/<id>/report.xlsx  protected action plan input workbook (action plan page notes pre-filled)
    GET    api/runs/<id>/notes        plans of all pages (each carries its "page") by brand / SKU / town + whether they can change
    POST   api/runs/<id>/notes        {"page": rescue|rebalance|loading-watch (default rescue), "scope", "brand", "material"?, "town"?, "categories", "remarks"}; empty removes
    PUT    api/runs/<id>/action-plan/check  returned workbook -> counts + acceptance token
    POST   api/runs/<id>/action-plan/accept  {"token"} -> rebuild downstream view
    GET    api/runs/<id>/settlement-check   month, batches and blank statuses
    POST   api/runs/<id>/settle       confirmed local history export + commit
    POST   api/runs/<id>/reopen       confirmed export removal + dependent invalidation
    POST   api/runs/<id>/discard      delete an unstarted run (failed upload)
    POST   api/runs/<id>/archive      /restore
    GET    api/slots                  the file slots
"""

from __future__ import annotations

import gzip
import json
import re
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from src import evolution
from ui.render import extract_legacy, render

from .runs import ROOT, SLOTS, RunError, Store

FALLBACK_HTML = ROOT / "ui" / "index.html"
ROUTE = re.compile(r"^/api/runs/(?P<id>RUN-\d{4,})(?:/(?P<rest>.*))?$")
EVOLUTION = re.compile(r"^(?P<lens>[a-z]+)$")
EVOLUTION_ASSETS = ROOT / "ui" / "evolution"
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


_GZIP_CACHE: dict[tuple[int, int], bytes] = {}


def _gzipped(body: bytes) -> bytes:
    """The dashboard page is tens of MB of inlined data and compresses about 6x; keep the last few results."""
    key = (len(body), zlib.crc32(body))
    if key not in _GZIP_CACHE:
        if len(_GZIP_CACHE) >= 3:
            _GZIP_CACHE.pop(next(iter(_GZIP_CACHE)))
        _GZIP_CACHE[key] = gzip.compress(body, compresslevel=6, mtime=0)
    return _GZIP_CACHE[key]


def _accepts_gzip(header: str) -> bool:
    for encoding in header.lower().split(','):
        name, *parameters = encoding.strip().split(';')
        if name.strip() != 'gzip':
            continue
        quality = next((p.split('=', 1)[1].strip() for p in parameters
                        if p.strip().startswith('q=')), '1')
        try:
            return 0 < float(quality) <= 1
        except ValueError:
            return False
    return False


def _length(header: str | None) -> int:
    """Content-Length as a non-negative int; anything malformed counts as 0."""
    return int(header) if header and header.isdigit() else 0


def make_server(store: Store, host: str = "127.0.0.1", port: int = 8770) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "fsne"

        # ---- helpers ---------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            # Large dashboards embed report data. Compress at the app boundary so
            # mounted previews and normal runs use the same host and API.
            if ctype.startswith('text/html') and len(body) > 1 << 20:
                extra = {**(extra or {}), 'Vary': 'Accept-Encoding'}
                if _accepts_gzip(self.headers.get('Accept-Encoding', '')):
                    body = _gzipped(body)
                    extra['Content-Encoding'] = 'gzip'
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: object, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _error(self, status: int, message: str) -> None:
            # A rejected upload may leave an unread request body. Do not parse
            # those bytes as another HTTP request on this connection.
            self.close_connection = True
            self._json({"error": message}, status)

        def _file(self, path: Path, ctype: str, name: str | None = None) -> None:
            if not path.is_file():
                return self._error(404, "Not found")
            return self._send(200, path.read_bytes(), ctype, {"Content-Disposition": f'attachment; filename="{name}"'} if name else None)

        def _cached(self, path: Path, ctype: str, extra: dict | None = None) -> None:
            """A static file with an ETag, so repeat visits cost a 304."""
            if not path.is_file():
                return self._error(404, "Not found")
            stat = path.stat()
            etag = f'"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
            headers = {"ETag": etag, "Cache-Control": "no-cache", **(extra or {})}
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                return None
            return self._send(200, path.read_bytes(), ctype, headers)

        def _evolution(self, run_id: str | None, rest: str, query: str) -> None:
            """Inventory graphs: <lens>[/rsl][/detail], with a stable selection for RSL drill-down."""
            lens, _, part = rest.partition("/")
            if (lens != "filters" and not EVOLUTION.match(lens)) or part not in {"", "detail", "rsl", "rsl/detail"} or (lens == "filters" and part):
                return self._error(404, "Not found")
            params = parse_qs(query)
            unit = params.get("unit", ["bdt"])[0]
            filters = {key: params[key] for key in evolution.FILTER_KEYS if key in params}
            as_of = params.get("as_of", [None])[0]
            selection = params.get("selection", [""])[0] if part.startswith("rsl") else None
            if run_id:
                run = store.path(run_id)
                history, data, out = run / "history", run / "input", run / "output" / "evolution"
            else:
                history, data, out = store.history_dir, ROOT / "sample_input", ROOT / "output" / "evolution"
            try:
                if lens == "filters":
                    return self._json(evolution.filter_options(history, data, out))
                with evolution.cached_artifact(history, data, out, lens, unit, filters, as_of=as_of, selection=selection) as path:
                    if part.endswith("detail"):
                        path = evolution.artifact_path(path.parent, lens, unit, "detail")
                    return self._cached(path, "application/json", {"Content-Encoding": "gzip"})
            except evolution.InvalidFilter as exc:
                return self._error(400, str(exc))
            except ValueError as exc:
                return self._error(404, str(exc))
            except FileNotFoundError as exc:
                raise RunError("No saved reports to chart") from exc

        def _body_json(self) -> dict:
            length = _length(self.headers.get("Content-Length"))
            if length > 1 << 16:
                raise RunError("Request too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except ValueError as exc:
                raise RunError("Body must be JSON") from exc
            if not isinstance(data, dict):
                raise RunError("Body must be a JSON object")
            return data

        def _user(self) -> str:
            return self.headers.get("Tailscale-User-Name") or self.headers.get("Tailscale-User-Login") or "local"

        def _dispatch(self, method: str) -> None:
            parsed = urlsplit(self.path)
            url, query = parsed.path, parsed.query
            try:
                if method in {"POST", "PUT"}:
                    origin = self.headers.get("Origin")
                    # Browsers may send writes only from the app's own origin.
                    if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                        return self._error(403, "Cross-origin writes are not allowed")
                    self.connection.settimeout(120)
                if method in {"GET", "HEAD"} and url in {"/", "/index.html"}:
                    return self._page(query)
                if method == "GET" and url == "/api/context":
                    run_id = parse_qs(query).get("run", [None])[0] or store.latest_ok()
                    meta = store.get(run_id) if run_id else None
                    return self._json({"run": meta, "sample": meta is None, "user": self._user()})
                if url == "/api/slots" and method == "GET":
                    return self._json([{"slot": k, "label": v[0], "stem": v[1], "ext": v[2], "required": k != "config"} for k, v in SLOTS.items()])
                if url == "/api/runs":
                    if method == "GET":
                        return self._json(store.list())
                    if method == "POST":
                        body = self._body_json()
                        return self._json(
                            store.create(
                                str(body.get("label", "")),
                                archive_previous=bool(body.get("archive_previous")), started_by=self._user(),
                            ),
                            201,
                        )
                if method in {"GET", "HEAD"} and url.startswith("/assets/evolution/"):
                    name = unquote(url.rsplit("/", 1)[-1])
                    if name != Path(name).name:
                        return self._error(404, "Not found")
                    return self._cached(EVOLUTION_ASSETS / name, ASSET_TYPES.get(Path(name).suffix, "application/octet-stream"))
                if method in {"GET", "HEAD"} and url.startswith("/api/evolution/"):
                    return self._evolution(None, url[len("/api/evolution/"):], query)
                m = ROUTE.match(url)
                if m:
                    return self._run_route(method, m["id"], m["rest"] or "", query)
                return self._error(404, "Not found")
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
                return None
            except RunError as exc:
                status = 404 if str(exc) == "Unknown run" else 400
                return self._error(status, str(exc))
            except Exception as exc:  # noqa: BLE001 - last resort, never leak a traceback to the page
                return self._error(500, f"Server error: {exc.__class__.__name__}")

        def _page(self, query: str) -> None:
            run_id = parse_qs(query).get("run", [None])[0]
            if run_id:
                meta = store.get(run_id)
                if meta["status"] != "ok":
                    raise RunError("That run has no results")
            else:
                run_id = store.latest_ok()
            if not run_id:
                if FALLBACK_HTML.is_file():
                    return self._file(FALLBACK_HTML, "text/html; charset=utf-8")
                return self._send(200, render().encode("utf-8"), "text/html; charset=utf-8")
            run = store.path(run_id)
            meta = store.get(run_id)
            payload = run / "data.json"
            legacy = run / "index.html"
            with store.lock:
                meta = store.get(run_id)
                if not payload.exists() and legacy.exists():
                    try:
                        data = extract_legacy(legacy.read_text())
                    except (ValueError, KeyError) as exc:
                        raise RunError("Could not read this older run's dashboard data") from exc
                    tmp = run / "data.json.tmp"
                    tmp.write_text(json.dumps(data, separators=(",", ":")))
                    tmp.replace(payload)
                    legacy.unlink()
                if not payload.exists():
                    return self._error(404, "Results data not found")
                try:
                    data = json.loads(payload.read_text())
                except (OSError, ValueError) as exc:
                    raise RunError("Could not read this run's dashboard data") from exc
            return self._send(200, render(data, meta).encode(), "text/html; charset=utf-8")

        def _run_route(self, method: str, run_id: str, rest: str, query: str = "") -> None:
            body = self._body_json() if method == "POST" else {}
            store.get(run_id)
            if rest.startswith("evolution/") and method in {"GET", "HEAD"}:
                return self._evolution(run_id, rest[len("evolution/"):], query)
            if not rest and method == "GET":
                return self._json(store.get(run_id))
            if rest == "log" and method == "GET":
                return self._send(200, store.log(run_id).encode(), "text/plain; charset=utf-8")
            if rest == "report.xlsx" and method == "GET":
                path = store.action_plan_path(run_id)
                run_date = store.get(run_id)["run_date"].replace("-", "_")
                return self._file(
                    path,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    f"action_plan_to_tm_{run_date}.xlsx",
                )
            if rest == "notes" and method == "GET":
                return self._json(store.plan_notes(run_id))
            if rest == "notes" and method == "POST":
                return self._json(store.save_plan_note(run_id, body, self._user()))
            if rest.startswith("files/") and method == "PUT":
                slot = rest[6:]
                filename = unquote(self.headers.get("X-Filename", ""))
                length = _length(self.headers.get("Content-Length"))
                return self._json(store.save_file(run_id, slot, filename, self.rfile, length))
            if rest == "config" and method == "POST":
                return self._json(store.configure(
                    run_id, str(body.get("report_date", "")), str(body["label"]) if "label" in body else None,
                    fsne_month=body.get("fsne_month"),
                ))
            if rest == "start" and method == "POST":
                return self._json(store.start(
                    run_id, str(body["label"]) if "label" in body else None, self._user(),
                    override_unsettled=body.get("override_unsettled") is True,
                ))
            if rest == "action-plan/check" and method == "PUT":
                return self._json(store.cycle.check(
                    run_id, unquote(self.headers.get("X-Filename", "")), self.rfile,
                    _length(self.headers.get("Content-Length")),
                ))
            if rest == "action-plan/accept" and method == "POST":
                return self._json(store.cycle.accept(run_id, str(body.get("token", "")), self._user()))
            if rest == "settlement-check" and method == "GET":
                with store.lock:
                    return self._json(store.cycle.settlement_check(run_id))
            if rest == "settle" and method == "POST":
                return self._json(store.cycle.settle(run_id, body, self._user()))
            if rest == "reopen" and method == "POST":
                return self._json(store.cycle.reopen(run_id, body.get("confirm") is True, self._user()))
            if rest == "cancel" and method == "POST":
                return self._json(store.cancel(run_id))
            if rest == "delete" and method == "POST":
                store.delete(run_id)
                return self._json({"deleted": run_id})
            if rest == "discard" and method == "POST":
                store.discard(run_id)
                return self._json({"discarded": run_id})
            if rest in {"archive", "restore"} and method == "POST":
                return self._json(store.archive(run_id, rest == "archive"))
            return self._error(404, "Not found")

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_HEAD(self) -> None:
            self._dispatch("HEAD")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def log_message(self, format: str, *args: object) -> None:  # Base-class signature; keep request logs quiet.
            return None

    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, port), Handler)

