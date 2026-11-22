"""Assemble the already-aggregated brand-status rows into UI JSON."""
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import polars as pl

from src.brand_status import METRICS, ORDER, aggregate, normalise
from src.io import date as parse_date
from src.movement import read_raw_reports
from src.stock_categories import CATEGORIES


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
    frames = read_raw_reports(history_dir)
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
    return with_categories(data, frames, current=current)


def _category_frames(history: Sequence[pl.DataFrame], current: pl.DataFrame | None) -> list[pl.DataFrame]:
    if current is None:
        return list(history)
    overlay = current.with_columns(parse_date('report_date'))
    if overlay['report_date'].n_unique() != 1:
        raise ValueError('Category overlay must contain one snapshot')
    snapshot = overlay['report_date'][0]
    measures = ['exposure_kl', 'lnd_fc_kl']
    updated = []
    for frame in history:
        frame = frame.with_columns(parse_date('report_date'))
        prior = frame.filter(pl.col('report_date') != snapshot)
        latest = frame.filter(pl.col('report_date') == snapshot)
        if latest.height:
            if (latest['batch_index'].n_unique() != latest.height
                    or overlay['batch_index'].n_unique() != overlay.height
                    or set(latest['batch_index']) != set(overlay['batch_index'])):
                raise ValueError('Category overlay batch identities differ from saved snapshot')
            latest = latest.drop(measures).join(overlay.select('batch_index', *measures),
                                               on='batch_index', how='left', validate='1:1')
            price = (pl.col('total_closing_stock_bdt').cast(pl.Float64)
                     / pl.col('total_closing_stock_kl').cast(pl.Float64))
            latest = latest.with_columns(
                (pl.col(name).cast(pl.Float64) * price).fill_nan(0).fill_null(0).alias(name.replace('_kl', '_bdt'))
                for name in measures
            )
            frame = pl.concat([prior, latest], how='diagonal_relaxed')
        updated.append(frame)
    return updated


def with_categories(status: dict, history: Sequence[pl.DataFrame], *, current: pl.DataFrame | None = None) -> dict:
    """Add category facts without changing existing report totals or frozen prices.

    Existing runs may have refreshed latest Exposure/LND measures. Read those
    quantities from their own frozen report and value them at the saved batch's
    price, retaining the private snapshot's accepted status. Earlier months are
    used verbatim. No pipeline calculations or report writes occur here.
    """
    frames = _category_frames(history, current)
    months = {datetime.strptime(label, '%b %Y').date(): i for i, label in enumerate(status['labels'])}
    groups = {group: i for i, group in enumerate(status['groups'])}
    facets = {}
    for unit in ('bdt', 'kl'):
        rows = aggregate(pl.concat([normalise(frame, unit) for frame in frames], how='vertical_relaxed'))
        for row in rows.iter_rows(named=True):
            i, g = months.get(row['month']), groups.get(row['rsl'])
            if i is None or g is None:
                continue
            key = (row['town'], row['brand'], row['parent'], row['category'])
            facet = facets.setdefault(key, {
                'town': row['town'], 'brand': row['brand'], 'parent': row['parent'],
                'pdesc': row['description'], 'category': row['category'],
                'm': [None] * len(months), 'm_kl': [None] * len(months),
            })
            field = 'm' if unit == 'bdt' else 'm_kl'
            if facet[field][i] is None:
                facet[field][i] = [[0.0, 0.0, 0.0] for _ in groups]
            facet[field][i][g] = [row[metric] for metric in METRICS]
    return {**status, 'categories': CATEGORIES, 'facets': list(facets.values())}
