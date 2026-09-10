"""Aggregate/read-only Wave 2.5 longitudinal readiness gate.

This audit measures the canonical event ledger after Waves 2.1-2.4 and composes
it with the existing 90-day Reported Concern recurrence eligibility audit.  It
never writes labels, predictions, training rows, client scores, or care state.

Run from an authorised Aura deployment shell:

    python scripts/audit_wave_2_5_readiness.py --format markdown
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
from typing import Any

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Connection


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_predictive_readiness import (  # noqa: E402
    _coerce_datetime,
    open_read_only_connection,
)
from scripts.audit_recurrence_target_readiness import (  # noqa: E402
    build_recurrence_readiness_report,
)


REPORT_VERSION = 1
TARGET_ID = "reported_concern_recurrence_90d_v1"

CURRENT_FAMILY_SUBJECTS = {
    "concern": "reported_concern",
    "consultation": "consultation",
    "assessment": "vehicle_assessment",
    "treatment": "treatment_plan",
    "treatment_action": "treatment_action",
    "driver_observation": "driver_checkin",
    "care_signal": "vehicle_health_alert",
    "priority": "priority_request",
    "evidence": "vehicle_evidence",
}

REQUIRED_EVENT_COLUMNS = {
    "car_id",
    "event_type",
    "subject_type",
    "subject_id",
    "occurred_at",
    "schema_version",
    "source",
    "progression_direction",
    "fingerprint",
}

# These are durable lifecycle completions/terminal outcomes, not predictions.
LIFECYCLE_OUTCOME_EVENTS = {
    "concern.resolved",
    "concern.reopened",
    "consultation.completed",
    "assessment.finalized",
    "treatment.completed",
    "treatment.outcome_recorded",
    "treatment_action.completed",
    "care_signal.resolved",
    "priority.resolved",
    "priority.cancelled",
}

# Keep mechanical/outcome evidence deliberately narrower than operational
# completion.  Operational closure must not masquerade as mechanical success.
MECHANICAL_OUTCOME_EVENTS = {
    "concern.resolved",
    "concern.reopened",
    "treatment.outcome_recorded",
}


def _event_family(event_type: object) -> str:
    value = "" if event_type is None else str(event_type).strip().lower()
    if not value:
        return "unknown"
    if "." in value:
        return value.split(".", 1)[0]
    if "_" in value:
        return value.split("_", 1)[0]
    return value


def _reflect(connection: Connection, name: str) -> Table | None:
    if name not in set(inspect(connection).get_table_names()):
        return None
    return Table(name, MetaData(), autoload_with=connection)


def _event_rows(connection: Connection, events: Table) -> list[dict[str, Any]]:
    columns = set(events.c.keys())
    wanted = (
        "car_id",
        "event_type",
        "subject_type",
        "subject_id",
        "occurred_at",
        "schema_version",
        "source",
        "progression_direction",
        "fingerprint",
        "visibility",
        "actor_type",
        "actor_authority",
    )
    selected = [events.c[name] for name in wanted if name in columns]
    return [dict(row) for row in connection.execute(select(*selected)).mappings().all()]


def _safe_distribution(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        raw = row.get(field)
        key = "(null)" if raw is None else str(raw)
        counts[key] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _span_days(values: list[datetime]) -> float | None:
    if not values:
        return None
    return round(max(0.0, (max(values) - min(values)).total_seconds() / 86400.0), 2)


def _family_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_family[_event_family(row.get("event_type"))].append(row)

    result: dict[str, dict[str, Any]] = {}
    for family in sorted(set(CURRENT_FAMILY_SUBJECTS) | set(by_family)):
        family_rows = by_family.get(family, [])
        vehicle_counts: Counter[object] = Counter(
            row.get("car_id") for row in family_rows if row.get("car_id") is not None
        )
        event_count = len(family_rows)
        top_vehicle_count = max(vehicle_counts.values(), default=0)
        times = [
            value
            for value in (_coerce_datetime(row.get("occurred_at")) for row in family_rows)
            if value is not None
        ]
        expected_subject = CURRENT_FAMILY_SUBJECTS.get(family)
        mismatch_count = 0
        if expected_subject is not None:
            mismatch_count = sum(
                1
                for row in family_rows
                if row.get("subject_type") != expected_subject
            )

        result[family] = {
            "event_count": event_count,
            "distinct_vehicles": len(vehicle_counts),
            "top_vehicle_share": (
                round(top_vehicle_count / event_count, 4) if event_count else None
            ),
            "observation_span_days": _span_days(times),
            "expected_subject_type": expected_subject,
            "subject_contract_mismatches": mismatch_count,
        }
    return result


def _longitudinal_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    vehicle_events: dict[object, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        car_id = row.get("car_id")
        if car_id is not None:
            vehicle_events[car_id].append(row)

    counts = [len(items) for items in vehicle_events.values()]
    spans: list[float] = []
    for items in vehicle_events.values():
        times = [
            value
            for value in (_coerce_datetime(item.get("occurred_at")) for item in items)
            if value is not None
        ]
        span = _span_days(times)
        if span is not None:
            spans.append(span)

    total = len(rows)
    top_vehicle_count = max(counts, default=0)
    return {
        "distinct_vehicles": len(vehicle_events),
        "vehicles_with_2_plus_events": sum(value >= 2 for value in counts),
        "vehicles_with_5_plus_events": sum(value >= 5 for value in counts),
        "vehicles_with_10_plus_events": sum(value >= 10 for value in counts),
        "median_events_per_vehicle": (
            float(statistics.median(counts)) if counts else 0.0
        ),
        "max_events_for_one_vehicle": top_vehicle_count,
        "top_vehicle_share_of_all_events": (
            round(top_vehicle_count / total, 4) if total else None
        ),
        "median_vehicle_observation_span_days": (
            round(float(statistics.median(spans)), 2) if spans else None
        ),
        "max_vehicle_observation_span_days": max(spans, default=None),
    }


def _outcome_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lifecycle = [
        row for row in rows if row.get("event_type") in LIFECYCLE_OUTCOME_EVENTS
    ]
    mechanical = [
        row for row in rows if row.get("event_type") in MECHANICAL_OUTCOME_EVENTS
    ]
    return {
        "lifecycle_outcome_events": len(lifecycle),
        "lifecycle_outcome_event_types": _safe_distribution(lifecycle, "event_type"),
        "distinct_vehicles_with_lifecycle_outcomes": len(
            {row.get("car_id") for row in lifecycle if row.get("car_id") is not None}
        ),
        "mechanical_outcome_events": len(mechanical),
        "mechanical_outcome_event_types": _safe_distribution(mechanical, "event_type"),
        "distinct_vehicles_with_mechanical_outcomes": len(
            {row.get("car_id") for row in mechanical if row.get("car_id") is not None}
        ),
        "progression_direction_distribution": _safe_distribution(
            rows, "progression_direction"
        ),
    }


def _event_ledger_report(connection: Connection) -> dict[str, Any]:
    events = _reflect(connection, "vehicle_events")
    if events is None:
        return {
            "present": False,
            "total_events": 0,
            "missing_required_columns": sorted(REQUIRED_EVENT_COLUMNS),
            "required_field_missingness": {},
            "event_type_counts": {},
            "source_counts": {},
            "visibility_counts": {},
            "actor_type_counts": {},
            "actor_authority_counts": {},
            "families": {},
            "current_family_coverage": {
                "covered": 0,
                "expected": len(CURRENT_FAMILY_SUBJECTS),
                "ratio": 0.0,
                "missing": sorted(CURRENT_FAMILY_SUBJECTS),
            },
            "longitudinal": _longitudinal_summary([]),
            "outcomes": _outcome_summary([]),
        }

    columns = set(events.c.keys())
    rows = _event_rows(connection, events)
    missing_columns = sorted(REQUIRED_EVENT_COLUMNS - columns)
    missingness = {
        field: sum(row.get(field) is None for row in rows)
        for field in sorted(REQUIRED_EVENT_COLUMNS & columns)
    }
    families = _family_summary(rows)
    missing_families = [
        family
        for family in CURRENT_FAMILY_SUBJECTS
        if families.get(family, {}).get("event_count", 0) == 0
    ]
    covered = len(CURRENT_FAMILY_SUBJECTS) - len(missing_families)

    return {
        "present": True,
        "total_events": len(rows),
        "missing_required_columns": missing_columns,
        "required_field_missingness": missingness,
        "event_type_counts": _safe_distribution(rows, "event_type"),
        "source_counts": _safe_distribution(rows, "source"),
        "visibility_counts": _safe_distribution(rows, "visibility"),
        "actor_type_counts": _safe_distribution(rows, "actor_type"),
        "actor_authority_counts": _safe_distribution(rows, "actor_authority"),
        "families": families,
        "current_family_coverage": {
            "covered": covered,
            "expected": len(CURRENT_FAMILY_SUBJECTS),
            "ratio": round(covered / len(CURRENT_FAMILY_SUBJECTS), 4),
            "missing": missing_families,
        },
        "longitudinal": _longitudinal_summary(rows),
        "outcomes": _outcome_summary(rows),
    }


def _integrity_constraints(ledger: dict[str, Any]) -> list[str]:
    constraints: list[str] = []
    if not ledger.get("present"):
        constraints.append("vehicle_events_table_missing")
    if ledger.get("missing_required_columns"):
        constraints.append("canonical_event_contract_incomplete")
    if any(ledger.get("required_field_missingness", {}).values()):
        constraints.append("canonical_event_required_field_missingness_detected")
    mismatches = sum(
        int(summary.get("subject_contract_mismatches", 0))
        for summary in ledger.get("families", {}).values()
    )
    if mismatches:
        constraints.append("canonical_event_subject_contract_mismatch_detected")
    return constraints


def build_wave_2_5_readiness_report(
    connection: Connection,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build one aggregate Wave 2.5 report without mutating source data."""

    ledger = _event_ledger_report(connection)
    recurrence = build_recurrence_readiness_report(connection, as_of=as_of)
    integrity_constraints = _integrity_constraints(ledger)
    target_constraints = list(recurrence.get("constraints", []))

    if integrity_constraints:
        decision = "defer"
        decision_basis = (
            "Canonical event integrity must be repaired before target evaluation can "
            "advance. No rules baseline is permitted from structurally unreliable data."
        )
    elif target_constraints:
        decision = "collect_more_data"
        decision_basis = (
            "The canonical ledger is measurable, but the approved 90-day concern "
            "recurrence target still lacks the real multi-vehicle follow-up/outcome "
            "coverage required by its contract."
        )
    else:
        decision = "proceed_to_rules_baseline"
        decision_basis = (
            "The automated structural and target-eligibility blockers are clear. This "
            "permits only an offline deterministic rules-baseline evaluation under the "
            "existing Wave 1.5 contract; it does not approve predictive deployment."
        )

    coverage = ledger.get("current_family_coverage", {})
    advisory_notes: list[str] = []
    if coverage.get("missing"):
        advisory_notes.append("current_canonical_family_coverage_is_incomplete")
    longitudinal = ledger.get("longitudinal", {})
    if (longitudinal.get("distinct_vehicles") or 0) < 2:
        advisory_notes.append("canonical_ledger_is_single_vehicle_or_empty")
    outcomes = ledger.get("outcomes", {})
    if (outcomes.get("mechanical_outcome_events") or 0) == 0:
        advisory_notes.append("no_mechanical_outcome_events_observed")

    return {
        "report_version": REPORT_VERSION,
        "wave": "2.5",
        "target_id": TARGET_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_backend": connection.dialect.name,
        "privacy": {
            "aggregate_only": True,
            "vehicle_ids_included": False,
            "subject_ids_included": False,
            "user_identifiers_included": False,
            "free_text_included": False,
            "raw_event_payloads_included": False,
            "raw_media_identifiers_included": False,
        },
        "decision": decision,
        "predictive_implementation_approved": False,
        "rules_baseline_evaluation_permitted": decision == "proceed_to_rules_baseline",
        "decision_basis": decision_basis,
        "integrity_constraints": integrity_constraints,
        "target_constraints": target_constraints,
        "advisory_notes": sorted(set(advisory_notes)),
        "canonical_ledger": ledger,
        "recurrence_target": {
            "target_id": recurrence.get("target_id"),
            "as_of": recurrence.get("as_of"),
            "episodes": recurrence.get("episodes", {}),
            "constraints": target_constraints,
        },
        "next_gate": (
            "repair_canonical_event_integrity"
            if decision == "defer"
            else "continue_real_longitudinal_collection_and_rerun"
            if decision == "collect_more_data"
            else "offline_deterministic_rules_baseline_evaluation"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    ledger = report["canonical_ledger"]
    coverage = ledger["current_family_coverage"]
    longitudinal = ledger["longitudinal"]
    outcomes = ledger["outcomes"]
    recurrence = report["recurrence_target"]["episodes"]

    lines = [
        "# Aura Wave 2.5 — Longitudinal Coverage and Data-Quality Gate",
        "",
        f"Target: `{report['target_id']}`",
        f"Database: `{report['database_backend']}`",
        f"Decision: **{report['decision']}**",
        f"Predictive implementation approved: **{report['predictive_implementation_approved']}**",
        f"Rules-baseline evaluation permitted: **{report['rules_baseline_evaluation_permitted']}**",
        "",
        "## Canonical ledger",
        "",
        f"- Total canonical events: `{ledger['total_events']}`",
        f"- Distinct vehicles: `{longitudinal['distinct_vehicles']}`",
        f"- Current family coverage: `{coverage['covered']}/{coverage['expected']}` (`{coverage['ratio']}`)",
        f"- Missing current families: `{', '.join(coverage['missing']) if coverage['missing'] else 'none'}`",
        f"- Median events per represented vehicle: `{longitudinal['median_events_per_vehicle']}`",
        f"- Top-vehicle share of all events: `{longitudinal['top_vehicle_share_of_all_events']}`",
        f"- Median observation span (days): `{longitudinal['median_vehicle_observation_span_days']}`",
        f"- Max observation span (days): `{longitudinal['max_vehicle_observation_span_days']}`",
        "",
        "## Current canonical family coverage",
        "",
    ]
    for family, summary in ledger["families"].items():
        if family not in CURRENT_FAMILY_SUBJECTS and summary["event_count"] == 0:
            continue
        lines.append(
            f"- `{family}`: events `{summary['event_count']}`, vehicles "
            f"`{summary['distinct_vehicles']}`, top-vehicle share "
            f"`{summary['top_vehicle_share']}`, subject mismatches "
            f"`{summary['subject_contract_mismatches']}`"
        )

    lines.extend(
        [
            "",
            "## Outcome coverage",
            "",
            f"- Lifecycle outcome events: `{outcomes['lifecycle_outcome_events']}`",
            f"- Vehicles with lifecycle outcomes: `{outcomes['distinct_vehicles_with_lifecycle_outcomes']}`",
            f"- Mechanical outcome events: `{outcomes['mechanical_outcome_events']}`",
            f"- Vehicles with mechanical outcomes: `{outcomes['distinct_vehicles_with_mechanical_outcomes']}`",
            "",
            "## 90-day concern recurrence target",
            "",
            f"- Resolved episodes: `{recurrence.get('resolved_episodes_total', 0)}`",
            f"- Distinct vehicles with resolved episodes: `{recurrence.get('distinct_vehicles_with_resolved_episodes', 0)}`",
            f"- Completed 90-day windows: `{recurrence.get('completed_90_day_windows', 0)}`",
            f"- Positive recurrence outcomes: `{recurrence.get('positive_recurrence', 0)}`",
            f"- Observed non-recurrence outcomes: `{recurrence.get('negative_observed', 0)}`",
            f"- Labelled outcomes: `{recurrence.get('labelled_outcomes_total', 0)}`",
            f"- Censored outcomes: `{recurrence.get('censored_total', 0)}`",
            "",
            "## Integrity constraints",
            "",
        ]
    )
    if report["integrity_constraints"]:
        lines.extend(f"- `{item}`" for item in report["integrity_constraints"])
    else:
        lines.append("- none detected")

    lines.extend(["", "## Target constraints", ""])
    if report["target_constraints"]:
        lines.extend(f"- `{item}`" for item in report["target_constraints"])
    else:
        lines.append("- none detected by the target-specific structural audit")

    lines.extend(["", "## Advisory notes", ""])
    if report["advisory_notes"]:
        lines.extend(f"- `{item}`" for item in report["advisory_notes"])
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Decision boundary",
            "",
            report["decision_basis"],
            "",
            "Even `proceed_to_rules_baseline` permits only offline deterministic "
            "evaluation. It does not authorize a model, prediction API, client-facing "
            "score, Rina predictive behavior, autonomous care action, or deployment.",
            "",
            "## Privacy boundary",
            "",
            "This report is aggregate-only. It omits vehicle/subject/user identifiers, "
            "free text, raw event payloads and media identifiers.",
            "",
            "## Next gate",
            "",
            f"`{report['next_gate']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_as_of(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--as-of",
        help="Optional ISO-8601 cutoff for deterministic follow-up classification.",
    )
    parser.add_argument("--output", help="Optional output file. Defaults to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from app import app  # noqa: PLC0415
    from extensions import db  # noqa: PLC0415

    with app.app_context(), open_read_only_connection(db.engine) as connection:
        report = build_wave_2_5_readiness_report(
            connection,
            as_of=_parse_as_of(args.as_of),
        )

    rendered = (
        render_markdown(report)
        if args.format == "markdown"
        else json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
