"""Manually reconcile Aura evidence metadata with the configured private store.

Run deliberately from an authenticated/controlled operational shell after the
Wave 1.4 storage variables are configured. The script never prints object keys,
media bytes or credentials.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from evidence.retrieval import (  # noqa: E402
    EvidenceRetrievalConfigurationError,
    reconcile_evidence_storage,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile private Aura evidence storage")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    with app.app_context():
        try:
            summary = reconcile_evidence_storage(
                storage_config=app.config,
                limit=args.limit,
            )
        except EvidenceRetrievalConfigurationError as exc:
            raise SystemExit("Evidence storage is not configured for reconciliation.") from exc

    print(
        "Evidence reconciliation complete: "
        f"examined={summary.examined} repaired={summary.repaired} "
        f"pending={summary.pending} failed={summary.failed}"
    )


if __name__ == "__main__":
    main()
