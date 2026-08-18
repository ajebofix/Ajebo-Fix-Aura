"""Generate an aggregate, read-only Wave 1.5 predictive-health readiness report.

This tool does not create predictions, export training rows or mutate Aura data.
Run it from an authorised deployment shell with Aura's normal database settings:

    python scripts/audit_predictive_readiness.py --format markdown

The report contains aggregate counts/distributions only. It never prints VINs,
vehicle/user identifiers, free text, media keys, hashes or raw event payloads.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import statistics
import sys
from typing import Any, Iterator

from sqlalchemy import MetaData, Table, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REPORT_VERSION = 1

TABLE_CANDIDATES = (
    "cars",
    "vehicle_events",
    "car_faults",
    "consultations",
    "vehicle_assessments",
    "assessments",
    "treatment_plans",
    "treatment_actions",
    "vehicle_dtcs",
    "diagnostic_code_definitions",
    "vehicle_recalls",
    "maintenance_schedules",
    "vehicle_evidence",
    "evidence_links",
    "evidence_extractions",
    "conversation_records",
    "chat_messages",
    "driver_checkins",
    "driver_check_ins",
    "health_snapshots",
    "vehicle_health_alerts",
)

# Wave 1.2 proved the canonical event pattern with Reported Concerns first.
# Other families are measured here as readiness coverage, never invented.
EXPECTED_CANONICAL_EVENT_FAMILIES = (
    "concern",
    "consultation",
    "assessment",
    "treatment",
    "dtc",
    "recall",
    "maintenance",
    "health",
    "driver",
    "conversation",
    "evidence",
)

STATUS_COLUMNS = (
    "status",
    "review_status",
    "storage_state",
    "is_open",
    "state",
)

PROVENANCE_COLUMNS = (
    "source",
    "source_channel",
    "vehicle_identity_source",
)

EVENT_MISSINGNESS_COLUMNS = (
    "event_type",
    "subject_type",
    "subject_id",
    "occurred_at",
    "schema_version",
    "source",
    "progression_direction",
    "fingerprint",
)


def _reflect_candidate_tables(connection: Connection) -> dict[str, Table]:
    existing = set(inspect(connection).get_table_names())
    metadata = MetaData()
    return {
        name: Table(name, metadata, autoload_with=connection)
        for name in TABLE_CANDIDATES
        if name in existing
    }


def _row_count(connection: Connection, table: Table) -> int:
    return int(connection.execute(select(func.count()).select_from(table)).scalar() or 0)


def _distinct_count(connection: Connection, table: Table, column_name: str) -> int:
    column = table.c[column_name]
    statement = select(func.count(func.distinct(column))).where(column.is_not(None))
    return int(connection.execute(statement).scalar() or 0)


def _missing_count(connection: Connection, table: Table, column_name: str) -> int:
    column = table.c[column_name]
    statement = select(func.count()).select_from(table).where(column.is_(None))
    return int(connection.execute(statement).scalar() or 0)


def _group_counts(
    connection: Connection,
    table: Table,
    column_name: str,
) -> dict[str, int]:
    column = table.c[column_name]
    statement = (
        select(column.label("value"), func.count().label("count"))
        .select_from(table)
        .group_by(column)
        .order_by(func.count().desc())
    )
    rows = connection.execute(statement).mappings()
    result: dict[str, int] = {}
    for row in rows:
        value = row["value"]
        key = "(null)" if value is None else str(value)
        result[key] = int(row["count"])
    return result


def _event_family(event_type: str) -> str:
    normalized = event_type.strip().lower()
    if "." in normalized:
        return normalized.split(".", 1)[0]
    if "_" in normalized:
        return normalized.split("_", 1)[0]
    return normalized or "unknown"


def _event_family_counts(event_type_counts: dict[str, int]) -> dict[str, int]:
    families: dict[str, int] = {}
    for event_type, count in event_type_counts.items():
        family = "unknown" if event_type == "(null)" else _event_family(event_type)
        families[family] = families.get(family, 0) + int(count)
    return dict(sorted(families.items(), key=lambda item: (-item[1], item[0])))


def _coerce_datetime(value: object) -> datetime | None:
    """Normalise reflected SQL temporal values without exposing row identity."""

    if value is None:
        return None

    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(candidate), time.min)
            except ValueError:
                return None

    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _event_longitudinal_summary(
    connection: Connection,
    table: Table,
) -> dict[str, Any]:
    columns = set(table.c.keys())
    if "car_id" not in columns:
        return {"available": False, "reason": "vehicle_events.car_id missing"}

    time_column_name = next(
        (
            candidate
            for candidate in ("occurred_at", "recorded_at", "created_at", "event_date")
            if candidate in columns
        ),
        None,
    )

    car_column = table.c.car_id
    count_statement = (
        select(car_column.label("car_id"), func.count().label("event_count"))
        .select_from(table)
        .group_by(car_column)
    )
    count_rows = connection.execute(count_statement).mappings().all()
    event_counts = [int(row["event_count"]) for row in count_rows]

    summary: dict[str, Any] = {
        "available": True,
        "vehicles_with_events": len(event_counts),
        "vehicles_with_2_plus_events": sum(value >= 2 for value in event_counts),
        "vehicles_with_3_plus_events": sum(value >= 3 for value in event_counts),
        "vehicles_with_5_plus_events": sum(value >= 5 for value in event_counts),
        "median_events_per_active_vehicle": (
            float(statistics.median(event_counts)) if event_counts else 0.0
        ),
        "max_events_for_one_vehicle": max(event_counts, default=0),
    }

    if time_column_name is None:
        summary.update(
            {
                "time_column": None,
                "earliest_event_at": None,
                "latest_event_at": None,
                "median_vehicle_observation_span_days": None,
                "max_vehicle_observation_span_days": None,
            }
        )
        return summary

    time_column = table.c[time_column_name]
    span_statement = (
        select(
            car_column.label("car_id"),
            func.min(time_column).label("first_at"),
            func.max(time_column).label("last_at"),
        )
        .select_from(table)
        .where(time_column.is_not(None))
        .group_by(car_column)
    )
    rows = connection.execute(span_statement).mappings().all()

    spans: list[float] = []
    earliest: datetime | None = None
    latest: datetime | None = None
    for row in rows:
        first_at = _coerce_datetime(row["first_at"])
        last_at = _coerce_datetime(row["last_at"])
        if first_at is None or last_at is None:
            continue
        if earliest is None or first_at < earliest:
            earliest = first_at
        if latest is None or last_at > latest:
            latest = last_at
        spans.append(max(0.0, (last_at - first_at).total_seconds() / 86400.0))

    summary.update(
        {
            "time_column": time_column_name,
            "earliest_event_at": _iso(earliest),
            "latest_event_at": _iso(latest),
            "median_vehicle_observation_span_days": (
                round(float(statistics.median(spans)), 2) if spans else None
            ),
            "max_vehicle_observation_span_days": (
                round(max(spans), 2) if spans else None
            ),
        }
    )
    return summary


def _distribution_if_present(
    connection: Connection,
    table: Table,
    candidates: tuple[str, ...],
) -> dict[str, dict[str, int]]:
    columns = set(table.c.keys())
    return {
        column: _group_counts(connection, table, column)
        for column in candidates
        if column in columns
    }


def build_readiness_report(connection: Connection) -> dict[str, Any]:
    """Build one aggregate, PII-free report from a read-only connection."""

    tables = _reflect_candidate_tables(connection)
    table_counts = {
        name: _row_count(connection, table)
        for name, table in tables.items()
    }

    event_report: dict[str, Any] = {
        "present": "vehicle_events" in tables,
        "total": table_counts.get("vehicle_events", 0),
    }
    event_families: dict[str, int] = {}

    if "vehicle_events" in tables:
        event_table = tables["vehicle_events"]
        event_columns = set(event_table.c.keys())
        distributions = _distribution_if_present(
            connection,
            event_table,
            (
                "event_type",
                "subject_type",
                "progression_direction",
                "source",
                "visibility",
                "schema_version",
            ),
        )
        event_report["distributions"] = distributions
        event_families = _event_family_counts(distributions.get("event_type", {}))
        event_report["families"] = event_families
        event_report["distinct_vehicles"] = (
            _distinct_count(connection, event_table, "car_id")
            if "car_id" in event_columns
            else 0
        )
        event_report["missingness"] = {
            column: _missing_count(connection, event_table, column)
            for column in EVENT_MISSINGNESS_COLUMNS
            if column in event_columns
        }
        event_report["longitudinal"] = _event_longitudinal_summary(
            connection,
            event_table,
        )
    else:
        event_report.update(
            {
                "distributions": {},
                "families": {},
                "distinct_vehicles": 0,
                "missingness": {},
                "longitudinal": {
                    "available": False,
                    "reason": "vehicle_events table absent",
                },
            }
        )

    status_distributions: dict[str, dict[str, dict[str, int]]] = {}
    provenance_distributions: dict[str, dict[str, dict[str, int]]] = {}
    for table_name, table in tables.items():
        status = _distribution_if_present(connection, table, STATUS_COLUMNS)
        if status:
            status_distributions[table_name] = status
        provenance = _distribution_if_present(connection, table, PROVENANCE_COLUMNS)
        if provenance:
            provenance_distributions[table_name] = provenance

    outcome_signals: dict[str, Any] = {
        "canonical_progression_directions": (
            event_report.get("distributions", {}).get("progression_direction", {})
        ),
        "reported_concern_statuses": status_distributions.get("car_faults", {}),
        "evidence_review_statuses": status_distributions.get("vehicle_evidence", {}).get(
            "review_status", {}
        ),
        "dtc_statuses": status_distributions.get("vehicle_dtcs", {}).get("status", {}),
        "maintenance_statuses": status_distributions.get(
            "maintenance_schedules", {}
        ).get("status", {}),
        "consultation_statuses": status_distributions.get("consultations", {}).get(
            "status", {}
        ),
        "treatment_plan_statuses": status_distributions.get("treatment_plans", {}).get(
            "status", {}
        ),
    }

    expected_family_coverage = {
        family: event_families.get(family, 0)
        for family in EXPECTED_CANONICAL_EVENT_FAMILIES
    }
    missing_families = [
        family for family, count in expected_family_coverage.items() if count == 0
    ]

    constraints = ["prediction_target_not_approved"]
    if event_report["total"] == 0:
        constraints.append("no_canonical_events")
    if missing_families:
        constraints.append("canonical_event_source_coverage_is_incomplete")

    longitudinal = event_report.get("longitudinal", {})
    if longitudinal.get("vehicles_with_2_plus_events", 0) == 0:
        constraints.append("no_multi_event_vehicle_history_observed")

    meaningful_directions = {
        key: value
        for key, value in outcome_signals["canonical_progression_directions"].items()
        if key not in {"(null)", "not_applicable", "insufficient_evidence"}
        and value > 0
    }
    if not meaningful_directions:
        constraints.append("no_mechanical_progression_outcomes_observed")

    return {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_backend": connection.dialect.name,
        "privacy": {
            "aggregate_only": True,
            "raw_vehicle_ids_included": False,
            "vin_included": False,
            "user_identifiers_included": False,
            "free_text_included": False,
            "raw_event_payloads_included": False,
            "evidence_storage_identifiers_included": False,
        },
        "recommendation": "collect_more_data",
        "proceed_allowed": False,
        "recommendation_basis": (
            "The opening Wave 1.5 audit cannot authorize predictive implementation "
            "until an exact prediction target contract is approved and the measured "
            "dataset is reviewed against that target."
        ),
        "constraints": sorted(set(constraints)),
        "table_counts": table_counts,
        "canonical_events": event_report,
        "expected_event_family_coverage": expected_family_coverage,
        "missing_expected_event_families": missing_families,
        "status_distributions": status_distributions,
        "provenance_distributions": provenance_distributions,
        "potential_outcome_signals": outcome_signals,
        "next_gate": {
            "required": "prediction_target_contract",
            "then": (
                "Review this aggregate inventory for target-specific label quality, "
                "leakage, missingness, privacy, class balance and follow-up coverage."
            ),
        },
    }


@contextmanager
def open_read_only_connection(engine: Engine) -> Iterator[Connection]:
    """Yield a connection whose current audit transaction rejects writes."""

    connection = engine.connect()
    transaction = connection.begin()
    try:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
        elif engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only = ON")
        yield connection
    finally:
        if transaction.is_active:
            transaction.rollback()
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only = OFF")
        connection.close()


def _markdown_mapping(mapping: dict[str, int], *, empty: str = "None") -> list[str]:
    if not mapping:
        return [f"- {empty}"]
    return [f"- `{key}`: {value}" for key, value in mapping.items()]


def render_markdown(report: dict[str, Any]) -> str:
    event_report = report["canonical_events"]
    longitudinal = event_report.get("longitudinal", {})
    lines = [
        "# Aura Wave 1.5 Predictive-Health Data-Readiness Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Database: `{report['database_backend']}`",
        f"Recommendation: **{report['recommendation']}**",
        f"Predictive implementation approved: **{report['proceed_allowed']}**",
        "",
        "## Decision basis",
        "",
        report["recommendation_basis"],
        "",
        "## Current constraints",
        "",
    ]
    lines.extend(f"- `{item}`" for item in report["constraints"])

    lines.extend(["", "## Aggregate table counts", ""])
    lines.extend(_markdown_mapping(report["table_counts"]))

    lines.extend(
        [
            "",
            "## Canonical event coverage",
            "",
            f"- Total canonical/event-ledger rows: {event_report['total']}",
            f"- Distinct vehicles represented: {event_report['distinct_vehicles']}",
            "",
            "### Event types",
            "",
        ]
    )
    lines.extend(
        _markdown_mapping(event_report.get("distributions", {}).get("event_type", {}))
    )

    lines.extend(["", "### Event families", ""])
    lines.extend(_markdown_mapping(event_report.get("families", {})))

    lines.extend(["", "### Reserved family coverage", ""])
    lines.extend(_markdown_mapping(report["expected_event_family_coverage"]))

    lines.extend(["", "## Longitudinal coverage", ""])
    if longitudinal.get("available"):
        for key in (
            "vehicles_with_events",
            "vehicles_with_2_plus_events",
            "vehicles_with_3_plus_events",
            "vehicles_with_5_plus_events",
            "median_events_per_active_vehicle",
            "max_events_for_one_vehicle",
            "earliest_event_at",
            "latest_event_at",
            "median_vehicle_observation_span_days",
            "max_vehicle_observation_span_days",
        ):
            lines.append(f"- {key}: `{longitudinal.get(key)}`")
    else:
        lines.append(f"- unavailable: {longitudinal.get('reason', 'unknown reason')}")

    lines.extend(["", "## Canonical event missingness", ""])
    lines.extend(_markdown_mapping(event_report.get("missingness", {})))

    lines.extend(["", "## Progression-direction signals", ""])
    lines.extend(
        _markdown_mapping(
            report["potential_outcome_signals"]["canonical_progression_directions"]
        )
    )

    lines.extend(
        [
            "",
            "## Privacy boundary",
            "",
            (
                "This report contains aggregates only. It deliberately omits vehicle "
                "IDs, VINs, user identifiers, free text, raw event payloads and "
                "evidence-storage identifiers."
            ),
            "",
            "## Next gate",
            "",
            f"Required: **{report['next_gate']['required']}**",
            "",
            report["next_gate"]["then"],
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a read-only aggregate Wave 1.5 readiness report."
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file. If omitted, print to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Delay application construction so unit tests can exercise the report
    # builder without creating Aura's full Flask runtime.
    from app import app  # noqa: PLC0415
    from extensions import db  # noqa: PLC0415

    with app.app_context(), open_read_only_connection(db.engine) as connection:
        report = build_readiness_report(connection)

    rendered = (
        json.dumps(report, indent=2, sort_keys=True)
        if args.format == "json"
        else render_markdown(report)
    )

    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
