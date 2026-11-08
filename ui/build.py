"""Assemble ui/index.html from template.html + data computed by the pipeline.

  uv run python -m src movement_series --history-dir history --output output/movement_series.parquet
  uv run python -m src movement_parents --history-dir history --output output/movement_parents.parquet
  uv run python ui/build.py
"""
import datetime as dt
import json
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from src.movement import (
    BRAND,
    RSL,
    read_reports,
    transform_parent_series,
    transform_series,
)
from ui.run_rate_data import build_current
from ui.sales_push_data import build_payload as sales_push_payload
from ui.status_data import build_payload
from ui.transfer_data import build_payload as transfer_payload

d = pathlib.Path(__file__).parent
src = d.parent / 'output' / 'movement_series.parquet'
if not src.exists():
    raise SystemExit(f'{src} missing: run the movement_series pipeline first')

s = pl.read_parquet(src).sort('month')
months = s['month'].unique().sort().to_list()
def r2(v):
    return None if v is None else round(v, 2)
history = read_reports(d.parent / 'history')
volume_series = transform_series(history, keys=(BRAND, RSL), stock_column='total_closing_stock_kl')
volume_parents = transform_parent_series(history, rsl=True, stock_column='total_closing_stock_kl')
volume_by = {(r['brand'], r.get('parent_code'), r['month'], r['rsl_group']): r
             for frame in [volume_series, volume_parents] for r in frame.iter_rows(named=True)}
def cell(r):
    v = volume_by[(r['brand'], r.get('parent_code'), r['month'], r['rsl_group'])]
    return {'lm': r2(r['lm_stock_bdt']), 'o': r2(r['cm_no_new_bdt']), 'n': r2(r['cm_new_bdt']),
            'r': r2(r['reduced_bdt']), 'rem': r['remarks'],
            'kl': {'lm': v['lm_stock_bdt'], 'o': v['cm_no_new_bdt'],
                   'n': v['cm_new_bdt'], 'r': v['reduced_bdt']}}
# RSL groups come from the pipeline (src/report.py), shortest shelf life first
ORDER = ['< 3M', '3M - 6M', '6M - 10M', '> 10M']
present = set(s['rsl_group'].unique().to_list())
groups = [g for g in ORDER if g in present] + sorted(present - set(ORDER))
gi = {g: i for i, g in enumerate(groups)}
def slot(store, month, r):
    """store[month] is a list with one cell (or None) per RSL group."""
    store.setdefault(month, [None] * len(groups))[gi[r['rsl_group']]] = cell(r)
by = {}
for r in s.iter_rows(named=True):
    slot(by.setdefault(r['brand'], {}), r['month'], r)
par_src = d.parent / 'output' / 'movement_parents.parquet'
if not par_src.exists():
    raise SystemExit(f'{par_src} missing: run the movement_parents pipeline first')
par = {}
for r in pl.read_parquet(par_src).iter_rows(named=True):
    p = par.setdefault(r['brand'], {}).setdefault(r['parent_code'], {'c': r['parent_code'], 'd': r['parent_desc'], 'by': {}})
    slot(p['by'], r['month'], r)
total = by.pop('Grand Total')
first = months[0]
start = (first.replace(day=1) - dt.timedelta(days=1)).strftime('%b')
series = {'labels': [m.strftime('%b') for m in months], 'start': start, 'year': months[-1].year,
          'groups': groups,
          'total': [total[m] for m in months],
          'brands': [{'b': b, 'm': [v.get(m) for m in months],
                      'p': [{'c': p['c'], 'd': p['d'], 'm': [p['by'].get(m) for m in months]} for p in par.get(b, {}).values()]}
                     for b, v in by.items()]}
latest_saved = None
for path in sorted((d.parent / 'history').glob('*.xlsx'), reverse=True):
    candidate = pl.read_excel(path, sheet_id=1, infer_schema_length=0)
    dates = candidate['report_date'].cast(pl.String).str.slice(0, 10).str.to_date()
    if months[-1] in dates:
        latest_saved = candidate
        break
if latest_saved is None:
    raise ValueError('Latest movement snapshot is absent from saved reports')
current, refreshed, doh, impact = build_current(d.parent / 'sample_input', latest_saved, months[-1], d.parent / 'output')
(d.parent / 'output' / 'run_rate_impact.json').write_text(json.dumps(impact, indent=2) + '\n')
(d.parent / 'output' / 'current_measures.json').write_text(json.dumps(impact['measures_after'], indent=2) + '\n')
series['doh'] = doh
batch = json.dumps(current.sort('total_closing_stock_bdt', descending=True, nulls_last=True).head(400).to_dicts(), separators=(',', ':'), allow_nan=False)
t = (d / 'template.html').read_text().replace('/*DOH_OVERLAY*/', (d / 'doh_overlay.js').read_text())
# Match the movement report's month window, including its opening snapshot.
opening = (months[0].replace(day=1) - dt.timedelta(days=1)).replace(day=1)
status = build_payload(d.parent / 'history', [opening, *[m.replace(day=1) for m in months]], current=refreshed)
transfer = transfer_payload(current, d.parent / 'sample_input', d.parent / 'output')
sales_push = sales_push_payload(d.parent / 'sample_input', current)
t = (t.replace('/*SALESPUSH*/{}', json.dumps(sales_push, separators=(',', ':'), allow_nan=False, default=str).replace('<', '\\u003c'))
     .replace('/*TRANSFER*/{}', json.dumps(transfer, separators=(',', ':'), allow_nan=False, default=str).replace('<', '\\u003c'))
     .replace('/*BATCH*/[]', batch.replace('<', '\\u003c'))
     .replace('/*SERIES*/{}', json.dumps(series, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c'))
     .replace('/*MEASURES*/{}', json.dumps(impact['measures_after'], separators=(',', ':'), allow_nan=False))
     .replace('/*STATUS*/{}', json.dumps(status, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c')))
(d / 'index.html').write_text(t)
print(f'built: {len(series["brands"])} brands x {len(months)} months ({series["labels"][0]}-{series["labels"][-1]})')
