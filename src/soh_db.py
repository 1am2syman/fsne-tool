"""Translation of queries/soh_db.txt with native left joins."""

from pathlib import Path

import polars as pl

from . import ph, run_rate_parent
from .io import date, matching_files, number, promote_headers, raw_csvs

TEXT_COLUMNS = [
    "Distributor Name",
    "Brand Name",
    "Child Sku",
    "Batch code",
    "Primary First Date",
    "Primary Latest Date",
    "Expiry Slab",
    "Town",
]
INT_COLUMNS = [
    "Distributor code",
    "Material Code",
    "Pack Size",
    "NOS",
    "Shelf Life (DAYS)",
    "Shelf Life (Months)",
]
FLOAT_COLUMNS = [
    "Closing Stock",
    "Primary Sales",
    "Secondary Sales",
    "KL per unit",
    "CS",
    "DB Landing value",
    "Previous Month Closing Stock",
    "Previous N-1 Month Closing Stock",
    "Last 6 months average sales in KL",
]
COLUMNS = [
    "Distributor code",
    "Distributor Name",
    "Brand Name",
    "Material Code",
    "Child Sku",
    "Pack Size",
    "Batch code",
    "Primary First Date",
    "Primary Latest Date",
    "Expiry Date",
    "Closing Stock",
    "Primary Sales",
    "Secondary Sales",
    "Expiry Slab",
    "Town",
    "KL per unit",
    "NOS",
    "CS",
    "Shelf Life (DAYS)",
    "Shelf Life (Months)",
    "DB Landing value",
    "Previous Month Closing Stock",
    "Previous N-1 Month Closing Stock",
    "Last 6 months average sales in KL",
]


def transform(
    source: pl.DataFrame, hierarchy: pl.DataFrame, rates: pl.DataFrame
) -> pl.DataFrame:
    return (
        source.lazy()
        # M compares the CSV string to "0" before casting. "0.0" is retained.
        .filter((pl.col("Closing Stock").cast(pl.String) != "0").fill_null(True))
        .select(COLUMNS)
        .with_columns(
            pl.col("Expiry Date").cast(pl.String).str.replace_all("'", "", literal=True)
        )
        .with_columns(
            date("Expiry Date"),
            pl.col(TEXT_COLUMNS).cast(pl.String),
            *[number(name, pl.Int64) for name in INT_COLUMNS],
            *[number(name) for name in FLOAT_COLUMNS],
        )
        .join(
            hierarchy.lazy().select(
                "material_code", "material_desc", "parent_material_code"
            ),
            left_on="Material Code",
            right_on="material_code",
            how="left",
            nulls_equal=True,
            maintain_order="left",
        )
        .join(
            rates.lazy(),
            left_on=["parent_material_code", "Town"],
            right_on=["parent_material_code", "town"],
            how="left",
            nulls_equal=True,
            maintain_order="left",
        )
        # Unlike sec_history, M does NOT replace town spaces with underscores here.
        .with_columns(
            pl.concat_str(
                pl.col("Town").str.to_lowercase(),
                pl.col("Material Code").cast(pl.String),
                pl.col("Batch code"),
            ).alias("batch_index")
        )
        .collect()
    )


def build(data_dir: str | Path = "data/current", **_: object) -> pl.DataFrame:
    source = promote_headers(
        raw_csvs(
            matching_files(
                data_dir,
                lambda name: "shelflife" in name or "Shelflife" in name,
                excel=False,
            ),
            34,
        )
    )
    return transform(source, ph.build(data_dir), run_rate_parent.build(data_dir))


if __name__ == "__main__":
    from .cli import run

    run(build)
