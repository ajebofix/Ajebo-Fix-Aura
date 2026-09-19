"""Print Aura's registered Flask routes from the actual application url_map.

Usage:
    python scripts/snapshot_routes.py
    python scripts/snapshot_routes.py --format json

This is documentation tooling only. It does not mutate application or database state.
"""

from __future__ import annotations

import argparse
import json

from app import app


def _rows():
    rows = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        rows.append(
            {
                "rule": rule.rule,
                "methods": sorted(set(rule.methods) - {"HEAD", "OPTIONS"}),
                "endpoint": rule.endpoint,
            }
        )
    return sorted(rows, key=lambda item: (item["rule"], item["endpoint"]))


def _markdown(rows):
    print("| Methods | Route | Endpoint |")
    print("|---|---|---|")
    for row in rows:
        methods = ", ".join(row["methods"])
        print(f"| {methods} | `{row['rule']}` | `{row['endpoint']}` |")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )
    args = parser.parse_args()

    rows = _rows()
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return

    _markdown(rows)


if __name__ == "__main__":
    main()
