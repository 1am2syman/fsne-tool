"""Assemble the already-aggregated brand-status rows into UI JSON."""
from pathlib import Path

import polars as pl

from src.brand_status import METRICS, ORDER, aggregate, normalise


def payload(rows: pl.DataFrame, months: list) -> dict:
    present = set(rows['rsl'].unique().to_list())
    groups = [g for g in ORDER if g in present] + sorted(present - set(ORDER))
    mi, gi = {m: i for i, m in enumerate(months)}, {g: i for i, g in enumerate(groups)}

    def node(name, key, **extra):
        return {'name': name, 'id': key, 'm': [None] * len(months), 'kids': [], **extra}

    def add(n, r):
        i = mi[r['month']]
        if n['m'][i] is None:
            n['m'][i] = [[0.0, 0.0, 0.0] for _ in groups]
        cell = n['m'][i][gi[r['rsl']]]
        for j, metric in enumerate(METRICS):
            cell[j] += r[metric]

    total = node('All towns', 'total')
    towns, brands, parents = {}, {}, {}
    for r in rows.iter_rows(named=True):
        if r['month'] not in mi:
            continue
        t, b, p = r['town'], r['brand'], r['parent']
        if t not in towns:
            towns[t] = node(t, f't{len(towns)}', level=0)
            total['kids'].append(towns[t])
        if (t, b) not in brands:
            brands[t, b] = node(b, f'b{len(brands)}', level=1, town=t)
            towns[t]['kids'].append(brands[t, b])
        if (t, b, p) not in parents:
            parents[t, b, p] = node(r['description'], f'p{len(parents)}', level=2, town=t, brand=b, code=p)
            brands[t, b]['kids'].append(parents[t, b, p])
        for n in [total, towns[t], brands[t, b], parents[t, b, p]]:
            add(n, r)
    return {'labels': [m.strftime('%b %Y') for m in months], 'groups': groups, 'total': total}


def build_payload(history_dir: Path, months: list, current: pl.DataFrame | None = None) -> dict:
    frames = [pl.read_excel(p, sheet_id=1, infer_schema_length=0)
              for p in sorted(history_dir.glob('*.xlsx'))]
    if not frames:
        raise FileNotFoundError(f'No saved reports in {history_dir}')
    def facts(unit):
        saved = pl.concat([normalise(f, unit) for f in frames])
        if current is None:
            return saved
        latest = normalise(current, unit)
        if latest['date'].n_unique() != 1:
            raise ValueError('Current overlay must contain one snapshot')
        return pl.concat([saved.filter(pl.col('date') != latest['date'][0]), latest])

    data = payload(aggregate(facts('bdt')), months)
    volume = payload(aggregate(facts('kl')), months)
    if data['groups'] != volume['groups']:
        raise ValueError('Value and volume RSL groups differ')
    data['total_kl'] = volume['total']
    return data
