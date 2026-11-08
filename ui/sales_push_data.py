"""UI payload for sales-push feasibility: a bounded, precomputed scenario set.

The static page cannot run the solver, so it ships the declared scenarios from
src/sales_push_feasibility.py (one deadline) and only filters/selects them.
"""
from datetime import timedelta
from pathlib import Path

import polars as pl

from src import inventory_projection, report, soh_db
from src import sales_push_feasibility as sp
from src.run_rate_parent import source_through
from src.secondary import load

COLS = ['town', 'code', 'material', 'brand', 'batches', 'kl', 'week', 'simple', 'total', 'incr',
        'uplift', 'solver', 'review', 'reasons', 'resid', 'sales',
        'hcmp', 'peak', 'recent', 'win', 'obs', 'gap', 'rmo', 'adj', 'amb', 'pkm']


def r(v, n=4):
    return None if v is None else round(v, n)


def build_payload(data_dir: Path, current: pl.DataFrame) -> dict:
    as_of = report.EXCEL_EPOCH + timedelta(days=current.item(0, 'report_date'))
    stock = soh_db.build(data_dir, as_of=as_of)
    shares = inventory_projection.child_shares(source_through(load(data_dir), as_of))
    received = inventory_projection.received_dates(stock)
    evidence = sp.history_evidence(source_through(load(data_dir), as_of))
    req = sp.transform_requirements(current, shares, received, evidence=evidence)
    scen = sp.transform(current, shares, received)
    ids = [s.scenario_id for s in sp.SCENARIOS]
    names = current.group_by('Town', 'Material Code').agg(
        pl.col('material_desc').first(), pl.col('Brand Name').first())
    names = {(a, b): (c, d) for a, b, c, d in names.iter_rows()}
    grid = {}
    for row in scen.iter_rows(named=True):
        grid.setdefault((row['town'], row['material_code']), {})[row['scenario_id']] = row
    rows = []
    for q in req.iter_rows(named=True):
        key = (q['town'], q['material_code'])
        cells = grid[key]
        material, brand = names[key]
        rows.append([q['town'], q['material_code'], material, brand, q['target_batches'],
                     r(q['target_kl']), r(q['baseline_kl_week']), r(q['simple_required_kl_week']),
                     r(q['required_total_kl_week']), r(q['required_incremental_kl_week']),
                     r(q['required_uplift']), q['solver_status'], q['review_state'], q['reason_codes'],
                     [r(cells[i]['projected_residual_kl']) for i in ids],
                     [r(cells[i]['projected_sales_kl']) for i in ids],
                     q['hist_comparison'], r(q['hist_peak_kl_week'], 5), r(q['hist_recent_kl_week'], 5),
                     q['hist_window_months'], q['hist_observed_months'], q['hist_calendar_gap_months'],
                     q['hist_recent_months_observed'], q['hist_adjusted_months'],
                     q['hist_ambiguous_peak_months'], None if q['hist_peak_month'] is None else str(q['hist_peak_month'])[:7]])
    first = req.row(0, named=True)
    return {
        'snapshot': str(as_of), 'policy': sp.POLICY_VERSION, 'rate_basis': sp.RATE_BASIS,
        'deadline_kind': first['deadline_kind'], 'deadline_days': first['deadline_days'],
        'deadline_date': str(first['deadline_date']),
        'hist_caveats': sp.HIST_CAVEATS, 'max_uplift': sp.MAX_UPLIFT, 'tolerance': sp.UPLIFT_TOLERANCE,
        'scenarios': [{'id': s.scenario_id, 'mult': s.multiplier, 'delay': s.delay_days,
                       'duration': s.duration_days} for s in sp.SCENARIOS],
        'cols': COLS, 'rows': rows,
    }
