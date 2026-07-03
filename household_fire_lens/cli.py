from __future__ import annotations

import argparse
import json
import sys

from .aggregation import recompute_monthly_snapshots
from .classifier import classify_all
from .database import connect_database
from .importer import import_directory
from .server import DEFAULT_DB, main as server_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Household FIRE Lens command line tools.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    parser.add_argument("--host", help="Server host when running the dashboard.")
    parser.add_argument("--port", type=int, help="Server port when running the dashboard.")
    parser.add_argument("--import-dir", help="Recursively import supported files from a local archive directory.")
    parser.add_argument("--no-classify", action="store_true", help="Skip classification and snapshot recompute after import.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.import_dir:
        server_argv = [sys.argv[0], "--db", args.db]
        if args.host:
            server_argv.extend(["--host", args.host])
        if args.port:
            server_argv.extend(["--port", str(args.port)])
        original_argv = sys.argv
        try:
            sys.argv = server_argv
            server_main()
        finally:
            sys.argv = original_argv
        return
    conn = connect_database(args.db)
    try:
        report = import_directory(conn, args.import_dir)
        if not args.no_classify:
            report["classified"] = classify_all(conn)
            recompute_monthly_snapshots(conn)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
