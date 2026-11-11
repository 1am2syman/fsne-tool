"""Render the current dashboard template with an isolated run's data."""

import json
from html import escape
from pathlib import Path

from server.action_plans import decorate
from ui.samples import downloads

UI = Path(__file__).resolve().parent
NAMES = ("BATCH", "SERIES", "STATUS", "MEASURES", "TRANSFER", "SALESPUSH")


def render(payload: dict | None = None, run: dict | None = None) -> str:
    """Render reports when available, otherwise open the upload workspace."""
    has_report = payload is not None
    if payload is None:
        payload = {name: ([] if name == "BATCH" else {}) for name in NAMES}
    template = ((UI / "template.html").read_text(encoding="utf-8")
                .replace("/*DOH_OVERLAY*/", (UI / "doh_overlay.js").read_text(encoding="utf-8"))
                .replace("/*ONBOARDING*/", (UI / "onboarding.js").read_text(encoding="utf-8"))
                .replace("/*SAMPLES*/{}", json.dumps(downloads(), separators=(",", ":")))
                .replace("/*HAS_REPORT*/true", json.dumps(has_report)))
    identity = f"{run['id']} · {run['label']}" if run else "Sample" if has_report else "No report yet"
    snapshot = f"{payload['STATUS']['labels'][-1]} snapshot" if has_report else ""
    flag = decorate(dict(run)) if run else {"plan_state": "provisional", "view_flag": "Sample report · Pre-action plan view"}
    if run and run.get("out_of_date"):
        flag["view_flag"] += " · Out of date: a history month was reopened. Calculate a new run."
    if run and run.get("settlement") and run["plan_state"] == "settled" and not run["settlement"]["commit"].get("ok"):
        flag["view_flag"] += " · settled, not committed"
    template = (template.replace("<!--REPORT_IDENTITY-->Sample", escape(identity))
                .replace("<!--REPORT_SNAPSHOT-->", escape(snapshot))
                .replace("<!--PLAN_STATE-->", flag["plan_state"])
                .replace("<!--PLAN_FLAG-->", escape(flag["view_flag"])))
    for name in NAMES:
        empty = "[]" if name == "BATCH" else "{}"
        data = json.dumps(payload[name], separators=(",", ":"), allow_nan=False, default=str).replace("<", "\\u003c")
        template = template.replace(f"/*{name}*/{empty}", data)
    return template


def extract_legacy(html: str) -> dict:
    """Extract the embedded data from an existing generated run page."""
    result = {}
    for name in NAMES:
        prefix = f"const {name} = "
        start = html.index(prefix) + len(prefix)
        end = html.index(";\n", start)
        result[name] = json.loads(html[start:end])
    return result
