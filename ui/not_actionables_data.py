"""Full monthly Not actionable batch populations for the dashboard."""

import polars as pl

from src.not_actionables import RSL_GROUPS, cohort, monthly

COLS = ['batch_index', 'brand', 'parent', 'pdesc', 'material', 'mdesc', 'town', 'batch', 'rsl', 'days',
        'stock_bdt', 'stock_kl', 'status', 'plan']


def build_payload(history: list[pl.DataFrame]) -> dict:
    """Keep empty cohorts as real months and retain unrounded values in both units."""
    facts = monthly(history)
    selected = cohort(facts).sort('stock_bdt', descending=True)
    present = set(selected['rsl'].unique().to_list())
    groups = [*RSL_GROUPS, *sorted(present - set(RSL_GROUPS))]
    months = []
    for snapshot in facts['date'].unique().sort().to_list():
        rows = selected.filter(pl.col('date') == snapshot)
        months.append({'date': snapshot.isoformat(), 'label': snapshot.strftime('%b %Y'),
                       'rows': rows.select(COLS).rows()})
    return {'cols': COLS, 'groups': groups, 'months': months}
