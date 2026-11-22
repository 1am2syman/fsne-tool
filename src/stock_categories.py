"""Report stock categories, shared by status tables and evolution scopes."""
import polars as pl

CATEGORIES = ['Actionable', 'Non actionable', 'Incorrect stock', 'Good stock', 'Unclassified']


def category(status: pl.Expr) -> pl.Expr:
    """Keep unknown labels visible; legacy Actionable sublabels share one category."""
    text = status.cast(pl.String).str.strip_chars().str.to_lowercase().fill_null('')
    return (pl.when((text == 'actionable') | text.str.starts_with('actionable -'))
            .then(pl.lit('Actionable'))
            .when(text == 'not actionable').then(pl.lit('Non actionable'))
            .when(text == 'incorrect stock').then(pl.lit('Incorrect stock'))
            .when(text == 'rsl>10m_good stock').then(pl.lit('Good stock'))
            .otherwise(pl.lit('Unclassified')).alias('category'))


def status_column(frame: pl.DataFrame) -> pl.Expr:
    """Current planning field wins when both schemas are present."""
    name = next((c for c in ('mitigation_status', 'mitigation_status_category') if c in frame.columns), None)
    return pl.col(name) if name else pl.lit(None, dtype=pl.String)
