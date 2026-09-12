"""Validate Aura's Wave 2.5 recurrence gate against the synthetic cohort.

The output of this script is a test-harness result only. A pass demonstrates
that the recurrence gate can recognise positive recurrence, observed
non-recurrence and censoring across multiple synthetic vehicles. It does not
convert synthetic history into production evidence and never authorises a real
rules baseline or predictive deployment.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from scripts.audit_predictive_readiness import open_read_only_connection  # noqa: E402
from scripts.audit_wave_2_5_readiness import (  # noqa: E402
    build_wave_2_5_readiness_report,
)
from scripts.seed_wave_2_5_synthetic_cohort import (  # noqa: E402
    SYNTHETIC_DATASET,
    SYNTHETIC_SOURCE,
)


EXPECTED = {
    "resolved_episodes_total": 4,
    "distinct_vehicles_with_resolved_episodes": 4,
    "completed_90_day_windows": 3,
    "positive_recurrence": 2,
    "negative_observed": 1,
    "labelled_outcomes_total": 3,
    "censored_total": 1,
}


def build_synthetic_validation_result(
    connection,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Run the existing Wave 2.5 logic and wrap it as non-production evidence."""

    gate = build_wave_2_5_readiness_report(connection, as_of=as_of)
    episodes = gate["recurrence_target"]["episodes"]

    metric_checks = {
        key: episodes.get(key) == expected
        for key, expected in EXPECTED.items()
    }
    expected_censoring = episodes.get("censoring") == {
        "censored_insufficient_followup": 1,
    }
    metric_checks["expected_censoring"] = expected_censoring

    passed = (
        gate.get("decision") == "proceed_to_rules_baseline"
        and not gate.get("integrity_constraints")
        and not gate.get("target_constraints")
        and all(metric_checks.values())
    )

    return {
        "validation_kind": "synthetic_longitudinal_gate_validation",
        "dataset": SYNTHETIC_DATASET,
        "source_marker": SYNTHETIC_SOURCE,
        "as_of": as_of.replace(tzinfo=timezone.utc).isoformat(),
        "status": "synthetic_validation_passed" if passed else "synthetic_validation_failed",
        "synthetic_only": True,
        "production_readiness_claim": False,
        "rules_baseline_evaluation_permitted": False,
        "predictive_implementation_approved": False,
        "underlying_gate_logic_result": gate.get("decision"),
        "metric_checks": metric_checks,
        "episodes": episodes,
        "integrity_constraints": gate.get("integrity_constraints", []),
        "target_constraints": gate.get("target_constraints", []),
        "note": (
            "A pass proves the Wave 2.5 recurrence gate works on an explicitly "
            "synthetic cohort. It is not real customer evidence and must not unblock "
            "production rules-baseline or predictive capability decisions."
        ),
    }


def render_markdown(result: dict[str, Any]) -> str:
    episodes = result["episodes"]
    checks = result["metric_checks"]
    lines = [
        "# Aura Wave 2.5 — Synthetic Longitudinal Gate Validation",
        "",
        f"Status: **{result['status']}**",
        f"Dataset: `{result['dataset']}`",
        f"Source marker: `{result['source_marker']}`",
        f"As of: `{result['as_of']}`",
        "",
        "## Safety boundary",
        "",
        "- Synthetic only: **True**",
        "- Production readiness claim: **False**",
        "- Rules-baseline evaluation permitted: **False**",
        "- Predictive implementation approved: **False**",
        "",
        "## Recurrence outcomes",
        "",
        f"- Resolved episodes: `{episodes['resolved_episodes_total']}`",
        f"- Vehicles with resolved episodes: `{episodes['distinct_vehicles_with_resolved_episodes']}`",
        f"- Completed 90-day windows: `{episodes['completed_90_day_windows']}`",
        f"- Positive recurrence outcomes: `{episodes['positive_recurrence']}`",
        f"- Observed non-recurrence outcomes: `{episodes['negative_observed']}`",
        f"- Labelled outcomes: `{episodes['labelled_outcomes_total']}`",
        f"- Censored outcomes: `{episodes['censored_total']}`",
        "",
        "## Expected-metric checks",
        "",
    ]
    lines.extend(f"- `{key}`: **{value}**" for key, value in checks.items())
    lines.extend(
        [
            "",
            "## Underlying gate logic",
            "",
            f"`{result['underlying_gate_logic_result']}`",
            "",
            result["note"],
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--as-of",
        default="2026-09-12T00:00:00",
        help="Deterministic ISO-8601 cutoff for the synthetic cohort.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    if as_of.tzinfo is not None:
        as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)

    with app.app_context(), open_read_only_connection(db.engine) as connection:
        result = build_synthetic_validation_result(connection, as_of=as_of)

    output = json.dumps(result, indent=2, sort_keys=True) if args.format == "json" else render_markdown(result)
    print(output)
    return 0 if result["status"] == "synthetic_validation_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
