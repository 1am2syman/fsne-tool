"""Start the app: uv run python -m server [--port 8770]."""

import argparse
import webbrowser

from .app import make_server
from .runs import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--open-browser", action="store_true", help="open the app in the default browser")
    args = parser.parse_args()
    store = Store()
    httpd = make_server(store, args.host, args.port)
    print(f"FSNE app on http://{args.host}:{args.port}/  (runs in {store.dir}, history in {store.history_dir})", flush=True)
    if args.open_browser:
        webbrowser.open(f"http://{args.host}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
