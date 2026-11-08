"""Run a translated query: python -m src QUERY [options]."""

import argparse

from . import (
    db_landing,
    history,
    inventory_projection,
    movement,
    ph,
    report,
    risk_calendar,
    run_rate_parent,
    run_rate_town,
    sales_push_feasibility,
    sec_history,
    soh_db,
    starting_date,
    transfer_opportunities,
)
from .cli import arguments, execute

QUERIES = {
    "db_landing": db_landing.build,
    "history": history.build,
    "inventory_projection": inventory_projection.build,
    "movement": movement.build,
    "movement_series": movement.build_series,
    "movement_parents": movement.build_parent_series,
    "ph": ph.build,
    "report": report.build,
    "risk_calendar": risk_calendar.build,
    "risk_calendar_summary": risk_calendar.build_summary,
    "run_rate_parent": run_rate_parent.build,
    "run_rate_town": run_rate_town.build,
    "sales_push_feasibility": sales_push_feasibility.build,
    "sales_push_requirements": sales_push_feasibility.build_requirements,
    "sec_history": sec_history.build,
    "soh_db": soh_db.build,
    "starting_date": starting_date.build,
    "transfer_opportunities": transfer_opportunities.build,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", choices=QUERIES)
    arguments(parser)
    args = parser.parse_args()
    execute(QUERIES[args.query], args)


if __name__ == "__main__":
    main()
