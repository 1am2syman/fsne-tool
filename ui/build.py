"""Assemble ui/index.html. Uses output/movement.parquet (from `python -m src movement`)
when present; otherwise falls back to the hand-pasted sample in move.txt."""
import json, pathlib
d = pathlib.Path(__file__).parent
t = (d / 'template.html').read_text()
mv = d.parent / 'output' / 'movement.parquet'
if mv.exists():
    import polars as pl
    f = pl.read_parquet(mv)
    def n(x, k=None): return '' if x is None else (f'{x*100:.0f}' if k == 'pct' else f'{x}')
    rows = f.filter(pl.col('brand') != 'Grand Total')
    move = '\n'.join('|'.join([r['brand'], n(r['lm_stock_bdt']), n(r['cm_no_new_bdt']), n(r['cm_new_bdt']),
                               n(r['reduced_bdt']), n(r['reduction_pct'], 'pct'), r['remarks'] or ''])
                     for r in rows.iter_rows(named=True))
    g = f.filter(pl.col('brand') == 'Grand Total').row(0, named=True)
    grand = {'lm': g['lm_stock_bdt'], 'cmo': g['cm_no_new_bdt'], 'cmn': g['cm_new_bdt'], 'red': g['reduced_bdt'],
             'pct': round(g['reduction_pct'] * 100), 'rem': g['remarks'] or ''}
    print('movement: computed from', mv.name)
else:
    move = (d / 'move.txt').read_text()
    grand = {"lm": 43218001.96, "cmo": 33680830.57, "cmn": 12594792.26, "red": 9537171.39, "pct": 22,
             "rem": "Seasonal sales push, Sales Push, Stock rotation Q2, Sales Push during winter, Regular sales, QDD ongoing, higher TLO ongoing, 2+1 offer ongoing, Liquidation through E-Com / MT / RTM"}
    print('movement: using pasted sample (move.txt)')
t = t.replace('/*BATCH*/[]', (d / 'batch.json').read_text()).replace('/*MOVE*/', move).replace('/*GRAND*/{}', json.dumps(grand))
(d / 'index.html').write_text(t)
