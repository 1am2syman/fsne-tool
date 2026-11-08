"""Compact source-backed monthly audit traces for DOH explanation overlays."""

import polars as pl

# Compact rows keep the static preview small; the browser uses this named schema.
FIELDS = ['raw_kl', 'adjusted_kl', 'expected_kl', 'upper_kl', 'season_factor',
          'peer_towns', 'reason', 'seasonal_supported', 'unseasoned_upper_kl', 'baseline_kl']


def audit_payload(audit: pl.DataFrame, pairs: pl.DataFrame) -> dict:
    """Calendar plus observed monthly traces, restricted to displayed rate keys.

    Missing pair-months are omitted, not confused with observed zero demand.
    Their zero contribution to the global-month denominator is explained in UI.
    Missing rates have no trace and stay explicitly unavailable.
    """
    months = audit['month'].unique().sort().to_list()
    keys = pairs.select('town', 'parent_material_code').unique()
    selected = audit.join(keys, on=['town', 'parent_material_code'], how='semi')
    mi = {m: i for i, m in enumerate(months)}
    nodes = {}
    for r in selected.iter_rows(named=True):
        key = (r['town'], r['parent_material_code'])
        node = nodes.setdefault(key, {'t': r['town'], 'p': str(r['parent_material_code']), 'm': {}})
        node['m'][str(mi[r['month']])] = [r[f] for f in FIELDS]
    return {'months': [m.isoformat() for m in months], 'fields': FIELDS, 'traces': list(nodes.values())}
