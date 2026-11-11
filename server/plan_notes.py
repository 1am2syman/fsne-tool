"""New action plans noted in the rescue meeting, by brand, SKU or town.

A plan is set on a scope: a brand, one SKU of a brand, or one town of that SKU. It
covers the scope's *uncovered* 3M–6M batches (no last-month plan), as listed by
the run's ``output/rescue_batches.parquet``. The nearest scope wins for a batch:
town over SKU over brand, replacing the wider plan rather than merging with it.

A plan is categories (``src.rescue.PLAN_CATEGORIES``, several allowed) plus free
remarks. Its batches' ``action_plan`` cell reads like sales' own remarks
(``"Sales push; Liquidation - E-Com; target 2 cases"``) and its
``mitigation_status`` is derived from the categories.

Plans are drafts: they pre-fill the action plan input file only. The uploaded and
accepted plan file stays the only way plans reach the snapshot and history.
Stored as ``plan_notes.json`` in the run folder; frozen once the month is settled.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

from src.rescue import PLAN_CATEGORIES, plan_status, plan_text

from .action_plans import now
from .errors import RunError

FILE = "plan_notes.json"
POPULATION = Path("output") / "rescue_batches.parquet"
SCOPES = ("brand", "sku", "town")
LEVEL_KEYS = {"brand": ["brand"], "sku": ["brand", "material"], "town": ["brand", "material", "town"]}
MAX_REMARKS = 300
VERSION = 2


def key(scope: str, brand: str, material: str | None, town: str | None) -> str:
    return json.dumps([scope, brand, material, town])


def read(run: Path) -> dict[str, dict]:
    path = run / FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise RunError("This run's plan notes are unreadable") from exc
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("plans"), dict):
        raise RunError("This run's plan notes are unreadable")
    return data["plans"]


def population(run: Path) -> pl.DataFrame:
    """Uncovered rescue batches: the only rows a plan note can reach."""
    path = run / POPULATION
    if not path.is_file():
        raise RunError("This run has no rescue data. Rebuild its dashboard to note plans.")
    return pl.read_parquet(path).filter(~pl.col("has_plan"))


def stamp(plans: dict[str, dict]) -> str:
    """Identity of what the input file shows; an empty plan set has no stamp."""
    if not plans:
        return ""
    values = {k: [v.get("categories"), v.get("remarks")] for k, v in plans.items()}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def resolve(batches: pl.DataFrame, plans: dict[str, dict]) -> pl.DataFrame:
    """One row per uncovered batch reached by a plan: action_plan, mitigation_status, scope."""
    out = batches.select("batch_index", *LEVEL_KEYS["town"])
    for scope in SCOPES:
        level = [v for v in plans.values() if v["scope"] == scope]
        columns = dict.fromkeys([*LEVEL_KEYS[scope], f"{scope}_plan", f"{scope}_status"], pl.String)
        frame = pl.DataFrame(
            [{**{c: v[c] for c in LEVEL_KEYS[scope]}, f"{scope}_plan": plan_text(v["categories"], v["remarks"]),
              f"{scope}_status": plan_status(v["categories"])} for v in level],
            schema=columns,
        )
        out = out.join(frame, on=LEVEL_KEYS[scope], how="left", validate="m:1")
    winner = pl.when(pl.col("town_plan").is_not_null() | pl.col("town_status").is_not_null())
    sku = pl.col("sku_plan").is_not_null() | pl.col("sku_status").is_not_null()
    return out.select(
        "batch_index",
        winner.then(pl.col("town_plan")).when(sku).then(pl.col("sku_plan")).otherwise(pl.col("brand_plan")).alias("action_plan"),
        winner.then(pl.col("town_status")).when(sku).then(pl.col("sku_status")).otherwise(pl.col("brand_status")).alias("mitigation_status"),
        winner.then(pl.lit("town")).when(sku).then(pl.lit("sku"))
        .when(pl.col("brand_plan").is_not_null()).then(pl.lit("brand")).alias("scope"),
    ).filter(pl.col("scope").is_not_null())


def entries(run: Path, plans: dict[str, dict]) -> pl.DataFrame | None:
    """Resolved plans as the input file's two entry columns, keyed by exact batch_index."""
    if not plans:
        return None
    return resolve(population(run), plans).select("batch_index", "action_plan", "mitigation_status")


def _text(body: dict, name: str) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RunError(f"{name} must be text")
    return value.strip() or None


def save(run: Path, meta: dict, body: dict, user: str) -> dict[str, dict]:
    """Validate and write one scope's plan; no categories and no remarks removes it. Caller holds the lock."""
    if meta.get("status") != "ok":
        raise RunError("That run has no completed report")
    if meta.get("plan_state") == "settled":
        raise RunError("This month is settled. Reopen it to change plan notes.")
    scope = body.get("scope")
    if scope not in SCOPES:
        raise RunError("A plan needs a scope: brand, sku or town")
    brand, material, town = _text(body, "brand"), _text(body, "material"), _text(body, "town")
    needed = {"brand": brand, "material": material, "town": town}
    if any(needed[c] is None for c in LEVEL_KEYS[scope]):
        raise RunError(f"A {scope} plan needs " + ", ".join(LEVEL_KEYS[scope]))
    categories = body.get("categories") or []
    names = [c for c, _ in PLAN_CATEGORIES]
    if not isinstance(categories, list) or any(c not in names for c in categories) or len(set(categories)) != len(categories):
        raise RunError("Choose action plan categories from the list")
    remarks = _text(body, "remarks")
    if remarks and len(remarks) > MAX_REMARKS:
        raise RunError(f"Remarks can hold at most {MAX_REMARKS} characters")
    batches = population(run)
    reach = batches.filter(*[pl.col(c) == needed[c] for c in LEVEL_KEYS[scope]])
    if reach.is_empty():
        raise RunError("That brand, SKU or town has no uncovered 3M–6M batches in this run")
    plans = read(run)
    k = key(scope, brand or "", material if scope != "brand" else None, town if scope == "town" else None)
    if categories or remarks:
        plans[k] = {"scope": scope, "brand": brand, "material": material if scope != "brand" else None,
                    "town": town if scope == "town" else None,
                    "categories": [c for c in names if c in categories], "remarks": remarks, "by": user, "at": now()}
    else:
        plans.pop(k, None)
    tmp = run / (FILE + ".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "plans": plans}, indent=1, sort_keys=True))
    tmp.replace(run / FILE)
    return plans
