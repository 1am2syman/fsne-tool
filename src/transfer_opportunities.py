"""Conservative transfer plans: compare leaving stock with moving it.

300 days remains the risk trigger. Above it, assess the days until that checkpoint;
at/below it, assess the days until expiry, without claiming the breach is undone.
Only transfers increasing combined sell-through in the two towns are accepted.
Destination sales must cover ALL its existing stock first (a conservative bound),
so an apparent benefit cannot merely displace another batch. Accepted moves update
shared pools before later suggestions. This is decision support, not routing.
"""
from datetime import timedelta
from pathlib import Path

import polars as pl

from . import report
from .inventory_projection import child_shares
from .run_rate_parent import source_through
from .run_rate_parent import transform as rate_transform
from .secondary import load

POLICY_VERSION = "transfer-benefit-v2"
STATUS = "beneficial transfer"
RSL_FLOOR_DAYS = 300
BASIS = "two-town stock left: leave versus transfer; shared child demand; conservative destination spare sales"
EPS = 1e-9
DEST_REASONS = {
    "expired_source": "Batch has already expired",
    "no_demand_evidence": "Receiving town has no known positive sales rate for this exact material",
    "unknown_source_rate": "Source sales rate is unknown; cannot compare leaving versus moving",
    "no_absorb_capacity": "No spare sales after allowing for stock already there and earlier transfers",
    "no_net_benefit": "Moving does not improve combined sell-through in the two towns",
    "displaces_existing_risk": "Would leave another batch with more stock at its own risk checkpoint",
    "source_sells_locally": "Remaining stock can sell locally by the assessment date",
    "source_allocated": "Available transfer quantity already assigned to earlier destinations",
    "no_other_town": "No other town with stock or sales history for this exact material",
}


def sources(rep: pl.DataFrame) -> pl.DataFrame:
    return rep.filter((pl.col("total_closing_stock_kl") > 0) & (pl.col("exposure_kl") > 0)).select(
        pl.col("Town").alias("source_town"), "Material Code", "material_desc", "Brand Name",
        pl.col("Batch code").alias("source_batch"), pl.col("rsl_days").alias("source_rsl_days"),
        pl.col("total_closing_stock_kl").alias("source_stock_kl"),
        pl.col("exposure_kl").alias("source_exposure_kl"),
        pl.col("exposure_bdt").alias("source_exposure_bdt"),
    )


def remaining(pool: list[tuple], rate: float, horizon: int) -> dict:
    """Continuous 30-day-month sales; earliest expiry then stable batch identity.

    Expired unsold stock stays in the result, not sold or silently discarded.
    No receipts, transit time or new demand are assumed.
    """
    time = 0.0
    left = {}
    for key, rsl, qty in sorted(pool, key=lambda b: (b[1], str(b[0]))):
        available = max(0.0, min(horizon, rsl) - time)
        sold = min(qty, available * rate / 30)
        left[key] = qty - sold
        if rate > 0:
            time += sold * 30 / rate
    return left


def _safe_quantity(pool: list[tuple], rate: float, key: tuple, rsl: int, limit: float) -> float:
    """Largest amount that does not worsen another batch's own checkpoint."""
    horizons = {max(0, b[1] - RSL_FLOOR_DAYS if b[1] > RSL_FLOOR_DAYS else b[1])
                for b in pool if b[2] > EPS}
    baseline = {h: remaining(pool, rate, h) for h in horizons}
    def safe(q):
        trial = pool + [(key, rsl, q)]
        for h, before in baseline.items():
            after = remaining(trial, rate, h)
            if any(after.get(k, 0) > v + 1e-12 for k, v in before.items()):
                return False
        return True
    if safe(limit):
        return limit
    lo, hi = 0.0, limit
    for _ in range(36):
        mid = (lo + hi) / 2
        if safe(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _move(pool: list[tuple], key: tuple, qty: float) -> list[tuple]:
    return [(k, r, max(0.0, v - qty) if k == key else v) for k, r, v in pool]


def transform(rep: pl.DataFrame, demand: pl.DataFrame | None = None) -> pl.DataFrame:
    """Plan deterministically, highest source exposure value first.

    demand columns: town, material_code, rate_kl_month. Production supplies parent
    rates split once by historical child sales share, including zero-stock towns.
    With no demand frame, avg_sec_m is allowed only for single-material families;
    ambiguous multi-child demand remains unknown, never repeated per child.
    """
    if rep.select('Town', 'Material Code', 'Batch code').is_duplicated().any():
        raise ValueError('Transfer stock must have unique town/material/batch identities')
    src = sources(rep).sort("source_exposure_bdt", "source_town", "source_batch",
                            descending=[True, False, False], nulls_last=True)
    if src.is_empty():
        return pl.DataFrame(schema=_schema())
    if demand is not None:
        if demand.select("town", "material_code").is_duplicated().any():
            raise ValueError("Transfer demand must be unique per town and exact material")
        if demand.filter(pl.col("rate_kl_month").is_not_null() &
                         ((pl.col("rate_kl_month") < 0) | ~pl.col("rate_kl_month").is_finite())).height:
            raise ValueError("Transfer demand must be finite and nonnegative")
    rates = {} if demand is None else {(r[0], r[1]): r[2] for r in demand.rows()}
    if demand is None:
        parents = {}
        for r in rep.rows(named=True):
            p = (r['Town'], r.get('parent_material_code', r['Material Code']))
            parents.setdefault(p, set()).add(r['Material Code'])
        for r in rep.rows(named=True):
            p = (r['Town'], r.get('parent_material_code', r['Material Code']))
            rates[r['Town'], r['Material Code']] = r['avg_sec_m'] if len(parents[p]) == 1 else None
    original = {}
    for r in rep.rows(named=True):
        p = (r['Town'], r['Material Code'])
        original.setdefault(p, []).append(((r['Town'], r['Material Code'], r['Batch code']),
                                           r['rsl_days'], r['total_closing_stock_kl']))
        rates.setdefault(p, None)
    working = {p: list(b) for p, b in original.items()}
    towns = {}
    for town, code in rates:
        towns.setdefault(code, set()).add(town)
    rows = []
    for s in src.rows(named=True):
        town, code, batch = s['source_town'], s['Material Code'], s['source_batch']
        key, poolkey = (town, code, batch), (town, code)
        rsl = s['source_rsl_days']
        horizon = max(0, rsl - RSL_FLOOR_DAYS if rsl > RSL_FLOOR_DAYS else rsl)
        metric = 'before_300_days' if rsl > RSL_FLOOR_DAYS else 'before_expiry'
        source_rate = rates.get(poolkey)
        baseline = None if source_rate is None else remaining(original[poolkey], source_rate, horizon).get(key, 0.0)
        budget = min(s['source_exposure_kl'], s['source_stock_kl'])
        candidates = sorted(towns.get(code, set()) - {town},
                            key=lambda t: (-(rates.get((t, code)) or 0), t))
        for dest in candidates or [None]:
            dp = (dest, code)
            dr = rates.get(dp)
            source_pool, dest_pool = working[poolkey], working.get(dp, [])
            stock = sum(b[2] for b in dest_pool)
            room = max(0.0, (dr or 0) * horizon / 30 - stock)
            reason = None
            if rsl <= 0:
                reason = 'expired_source'
            elif source_rate is None:
                reason = 'unknown_source_rate'
            elif dest is None:
                reason = 'no_other_town'
            elif dr is None or dr <= 0:
                reason = 'no_demand_evidence'
            local = {} if source_rate is None else remaining(source_pool, source_rate, horizon)
            if reason is None and budget <= EPS:
                reason = 'source_allocated'
            if reason is None and local.get(key, 0) <= EPS:
                reason = 'source_sells_locally'
            if reason is None and room <= EPS:
                reason = 'no_absorb_capacity'
            qty = 0.0
            before = after = gain = None
            if reason is None:
                assert source_rate is not None and dr is not None
                qty = _safe_quantity(dest_pool, dr, key, rsl, min(budget, local.get(key, 0), room))
                moved_source = _move(source_pool, key, qty)
                moved_dest = dest_pool + [(key, rsl, qty)]
                before = sum(local.values()) + sum(remaining(dest_pool, dr, horizon).values())
                after = sum(remaining(moved_source, source_rate, horizon).values()) + sum(remaining(moved_dest, dr, horizon).values())
                gain = before - after
                if qty <= 1e-7:
                    qty, reason = 0.0, 'displaces_existing_risk'
                elif gain <= EPS:
                    qty, reason = 0.0, 'no_net_benefit'
                else:
                    working[poolkey], working[dp] = moved_source, moved_dest
                    budget -= qty
            rows.append({**s, 'dest_town': dest, 'dest_stock_kl': stock,
                         'dest_min_rsl_days': min((b[1] for b in dest_pool), default=None),
                         'dest_has_older_stock': any(b[1] < rsl for b in dest_pool),
                         'dest_queue_ahead_kl': stock, 'dest_child_rate_kl_month': dr,
                         'dest_room_kl': room, 'sell_window_days': horizon,
                         'assessment_kind': metric, 'source_remaining_before_kl': baseline,
                         'network_remaining_before_kl': before, 'network_remaining_after_kl': after,
                         'net_benefit_kl': gain if reason is None else None,
                         'proposed_kl': qty if reason is None else None,
                         'rejection_reason': reason, 'status': STATUS if reason is None else None,
                         'basis': BASIS, 'policy_version': POLICY_VERSION})
    # Track each original source batch across all receiving pools after the plan.
    by_key = {}
    for p, pool in working.items():
        for key, _, _ in pool:
            by_key.setdefault(key, set()).add(p)
    final = {}
    for row in rows:
        key = (row['source_town'], row['Material Code'], row['source_batch'])
        h = row['sell_window_days']
        if key not in final:
            final[key] = None if row['source_remaining_before_kl'] is None else sum(
                remaining(working[p], rates[p], h).get(key, 0.0) for p in by_key[key])
        row['source_remaining_after_kl'] = final[key]
    return pl.DataFrame(rows, schema_overrides=_schema())


def _schema() -> dict:
    return {**dict.fromkeys(['source_town', 'material_desc', 'Brand Name', 'source_batch',
            'dest_town', 'assessment_kind', 'rejection_reason', 'status', 'basis', 'policy_version'], pl.String),
            **dict.fromkeys(['Material Code', 'source_rsl_days', 'dest_min_rsl_days', 'sell_window_days'], pl.Int64),
            'dest_has_older_stock': pl.Boolean,
            **dict.fromkeys(['source_stock_kl', 'source_exposure_kl', 'source_exposure_bdt',
                'dest_stock_kl', 'dest_queue_ahead_kl', 'dest_child_rate_kl_month', 'dest_room_kl',
                'source_remaining_before_kl', 'source_remaining_after_kl', 'network_remaining_before_kl',
                'network_remaining_after_kl', 'net_benefit_kl', 'proposed_kl'], pl.Float64)}


def eligible(candidates: pl.DataFrame) -> pl.DataFrame:
    return candidates.filter(pl.col('rejection_reason').is_null())


def demand_for(data_dir: Path, current: pl.DataFrame) -> pl.DataFrame:
    """All exact-material sales towns plus stock towns, with one shared parent rate."""
    as_of = report.EXCEL_EPOCH + timedelta(days=current.item(0, 'report_date'))
    source = source_through(load(data_dir), as_of)
    shares = child_shares(source)
    rates = rate_transform(source)
    stock_pairs = current.select(pl.col('Town').alias('town'),
        pl.col('Material Code').alias('material_code'), 'parent_material_code').unique()
    universe = pl.concat([shares.select(stock_pairs.columns), stock_pairs]).unique()
    return (universe.join(shares, on=stock_pairs.columns, how='left', validate='1:1')
        .join(rates, on=['town', 'parent_material_code'], how='left', validate='m:1')
        .select('town', 'material_code',
            (pl.col('avg_sec') * pl.col('share').fill_null(0.0)).alias('rate_kl_month')))


def build(data_dir: str | Path = 'data/current', **_: object) -> pl.DataFrame:
    current = report.build(data_dir)
    return transform(current, demand_for(Path(data_dir), current))
