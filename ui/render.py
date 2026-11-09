"""Render the current dashboard template with an isolated run's data."""

import json
from pathlib import Path

UI = Path(__file__).resolve().parent
NAMES = ("BATCH", "SERIES", "STATUS", "MEASURES", "TRANSFER", "SALESPUSH")


def render(payload: dict) -> str:
    template = ((UI / "template.html").read_text()
                .replace("/*DOH_OVERLAY*/", (UI / "doh_overlay.js").read_text())
                .replace("/*ONBOARDING*/", (UI / "onboarding.js").read_text()))
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
