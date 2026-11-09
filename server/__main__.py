"""Start the app: uv run python -m server [--port 8770]."""

import argparse

from .app import make_server
from .runs import RUNS_DIR, Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    store = Store()
    httpd = make_server(store, args.host, args.port)
    print(f"FSNE app on http://{args.host}:{args.port}/  (runs in {RUNS_DIR})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
