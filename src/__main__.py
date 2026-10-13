"""Run a translated query: python -m src QUERY [options]."""

import argparse

from . import (
    db_landing,
    history,
    movement,
    ph,
    report,
    run_rate_parent,
    run_rate_town,
    sec_history,
    soh_db,
    starting_date,
)
from .cli import arguments, execute

QUERIES = {
    "db_landing": db_landing.build,
    "history": history.build,
    "movement": movement.build,
    "ph": ph.build,
    "report": report.build,
    "run_rate_parent": run_rate_parent.build,
    "run_rate_town": run_rate_town.build,
    "sec_history": sec_history.build,
    "soh_db": soh_db.build,
    "starting_date": starting_date.build,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", choices=QUERIES)
    arguments(parser)
    args = parser.parse_args()
    execute(QUERIES[args.query], args)


if __name__ == "__main__":
    main()
