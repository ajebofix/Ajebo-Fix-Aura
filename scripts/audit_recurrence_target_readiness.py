"""Audit readiness for Aura's 90-day Reported Concern recurrence target.

This tool is read-only and aggregate-only. It evaluates whether production data
contains enough trustworthy resolved-concern episodes to support offline target
analysis. It does not create labels in production tables, predictions, training
exports, client-facing scores, or Rina behavior.

Run from an authorised deployment shell:

    python scripts/audit_recurrence_target_readiness.py --format markdown
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
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


REPORT_VERSION = 1
TARGET_ID = "reported_concern_recurrence_90d_v1"
TARGET_HORIZON_DAYS = 90
REQUIRED_EVENT_COLUMNS = {
    "id",
    "car_id",
    "event_type",
    "subject_type",
    "subject_id",
    "occurred_at",
}


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _reflect(connection: Connection, name: str) -> Table | None:
    if name not in set(inspect(connection).get_table_names()):
        return None
    return Table(name, MetaData(), autoload_with=connection)


def _event_rows(connection: Connection, events: Table) -> list[dict[str, Any]]:
    columns = set(events.c.keys())
    selected = [
        events.c[name]
        for name in (
            "id",
            "car_id",
            "ownership_id",
            "event_type",
            "subject_type",
            "subject_id",
            "occurred_at",
            "correction_of_event_id",
        )
        if name in columns
    ]
    statement = (
        select(*selected)
        .where(events.c.event_type.in_((
            "concern.resolved",
            "concern.reopened",
            "concern.corrected",
        )))
        .order_by(events.c.occurred_at.asc(), events.c.id.asc())
    )
    return [dict(row) for row in connection.execute(statement).mappings().all()]


def _ownership_rows(
    connection: Connection,
    ownership: Table | None,
) -> dict[int, dict[str, Any]]:
    if ownership is None or "id" not in ownership.c:
        return {}

    wanted = [
        ownership.c[name]
        for name in ("id", "car_id", "start_date", "end_date", "is_active")
        if name in ownership.c
    ]
    return {
        int(row["id"]): dict(row)
        for row in connection.execute(select(*wanted)).mappings().all()
    }


def _ownership_observable_through(
    row: dict[str, Any] | None,
    *,
    t0: datetime,
    horizon_end: datetime,
) -> tuple[bool, str | None]:
    if row is None:
        return False, "censored_history_incomplete"

    start_at = _coerce_datetime(row.get("start_date"))
    end_at = _coerce_datetime(row.get("end_date"))
    is_active = row.get("is_active")

    if start_at is not None and start_at > t0:
        return False, "censored_history_incomplete"
    if end_at is not None and end_at < horizon_end:
        return False, "censored_monitoring_ended"
    if end_at is None and is_active is False:
        return False, "censored_history_incomplete"
    return True, None


def build_recurrence_readiness_report(
    connection: Connection,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build an aggregate target-specific readiness report.

    Row-level event and ownership identities are used only inside this function
    to classify episodes. They are never emitted in the returned report.
    """

    as_of = _naive_utc(as_of or datetime.now(timezone.utc))
    events = _reflect(connection, "vehicle_events")
    ownership = _reflect(connection, "car_ownership")

    base_report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "target_id": TARGET_ID,
        "target_horizon_days": TARGET_HORIZON_DAYS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.replace(tzinfo=timezone.utc).isoformat(),
        "database_backend": connection.dialect.name,
        "privacy": {
            "aggregate_only": True,
            "vehicle_ids_included": False,
            "concern_ids_included": False,
            "user_identifiers_included": False,
            "free_text_included": False,
            "raw_event_payloads_included": False,
        },
        "predictive_implementation_approved": False,
        "recommendation": "collect_more_data",
    }

    if events is None:
        return {
            **base_report,
            "constraints": ["vehicle_events_table_missing"],
            "episodes": _empty_episode_summary(),
            "next_gate": "continue_real_longitudinal_collection",
        }

    event_columns = set(events.c.keys())
    missing_columns = sorted(REQUIRED_EVENT_COLUMNS - event_columns)
    if missing_columns:
        return {
            **base_report,
            "constraints": ["canonical_event_contract_incomplete"],
            "missing_required_event_columns": missing_columns,
            "episodes": _empty_episode_summary(),
            "next_gate": "repair_canonical_event_contract_before_target_evaluation",
        }

    rows = _event_rows(connection, events)
    ownership_by_id = _ownership_rows(connection, ownership)

    by_subject: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("subject_type") != "reported_concern":
            continue
        car_id = row.get("car_id")
        subject_id = row.get("subject_id")
        if car_id is None or subject_id is None:
            continue
        by_subject[(int(car_id), int(subject_id))].append(row)

    classification = Counter()
    labelled_by_vehicle: Counter[int] = Counter()
    resolved_by_vehicle: Counter[int] = Counter()
    full_windows = 0
    resolved_total = 0

    for (car_id, _subject_id), subject_rows in by_subject.items():
        resolved_rows = [
            row for row in subject_rows if row.get("event_type") == "concern.resolved"
        ]
        reopened_rows = [
            row for row in subject_rows if row.get("event_type") == "concern.reopened"
        ]
        corrected_rows = [
            row for row in subject_rows if row.get("event_type") == "concern.corrected"
        ]

        for resolved in resolved_rows:
            t0 = _coerce_datetime(resolved.get("occurred_at"))
            if t0 is None:
                classification["censored_history_incomplete"] += 1
                continue

            resolved_total += 1
            resolved_by_vehicle[car_id] += 1
            horizon_end = t0 + timedelta(days=TARGET_HORIZON_DAYS)

            reopen_candidates: list[tuple[datetime, dict[str, Any]]] = []
            for row in reopened_rows:
                reopened_at = _coerce_datetime(row.get("occurred_at"))
                if reopened_at is None:
                    continue
                if t0 < reopened_at <= horizon_end and reopened_at <= as_of:
                    reopen_candidates.append((reopened_at, row))

            correction_targets = {
                row.get("correction_of_event_id")
                for row in corrected_rows
                if row.get("correction_of_event_id") is not None
            }
            resolved_id = resolved.get("id")
            reopen_ids = {row.get("id") for _, row in reopen_candidates}
            ambiguous_correction = bool(
                resolved_id in correction_targets
                or any(reopen_id in correction_targets for reopen_id in reopen_ids)
            )

            # If a schema lacks correction target linkage, conservatively censor
            # when a correction for this subject occurs between t0 and the known
            # evaluation boundary.
            if "correction_of_event_id" not in event_columns:
                for row in corrected_rows:
                    corrected_at = _coerce_datetime(row.get("occurred_at"))
                    if corrected_at is not None and t0 <= corrected_at <= min(as_of, horizon_end):
                        ambiguous_correction = True
                        break

            if ambiguous_correction:
                classification["censored_corrected"] += 1
                continue

            if reopen_candidates:
                classification["positive_recurrence"] += 1
                labelled_by_vehicle[car_id] += 1
                if as_of >= horizon_end:
                    full_windows += 1
                continue

            if as_of < horizon_end:
                classification["censored_insufficient_followup"] += 1
                continue

            full_windows += 1
            ownership_row = None
            ownership_id = resolved.get("ownership_id")
            if ownership_id is not None:
                ownership_row = ownership_by_id.get(int(ownership_id))

            observable, censor_reason = _ownership_observable_through(
                ownership_row,
                t0=t0,
                horizon_end=horizon_end,
            )
            if not observable:
                classification[censor_reason or "censored_history_incomplete"] += 1
                continue

            classification["negative_observed"] += 1
            labelled_by_vehicle[car_id] += 1

    positive = int(classification["positive_recurrence"])
    negative = int(classification["negative_observed"])
    labelled_total = positive + negative
    censored_reasons = {
        key: int(value)
        for key, value in sorted(classification.items())
        if key.startswith("censored_") and value > 0
    }
    censored_total = sum(censored_reasons.values())

    labelled_vehicle_counts = list(labelled_by_vehicle.values())
    max_one_vehicle = max(labelled_vehicle_counts, default=0)
    top_vehicle_share = (
        round(max_one_vehicle / labelled_total, 4) if labelled_total else None
    )
    prevalence = round(positive / labelled_total, 4) if labelled_total else None

    constraints: list[str] = []
    if resolved_total == 0:
        constraints.append("no_eligible_resolved_concern_episodes")
    if full_windows == 0:
        constraints.append("no_completed_90_day_followup_windows")
    if labelled_total == 0:
        constraints.append("no_observed_target_outcomes")
    if positive == 0:
        constraints.append("no_positive_recurrence_outcomes_observed")
    if negative == 0:
        constraints.append("no_observed_non_recurrence_outcomes")
    if len(resolved_by_vehicle) < 2:
        constraints.append("single_vehicle_or_no_vehicle_cohort")

    return {
        **base_report,
        "constraints": constraints,
        "episodes": {
            "resolved_episodes_total": resolved_total,
            "distinct_vehicles_with_resolved_episodes": len(resolved_by_vehicle),
            "completed_90_day_windows": full_windows,
            "positive_recurrence": positive,
            "negative_observed": negative,
            "labelled_outcomes_total": labelled_total,
            "censored_total": censored_total,
            "censoring": censored_reasons,
            "recurrence_prevalence_among_observed": prevalence,
            "distinct_vehicles_with_labelled_outcomes": len(labelled_by_vehicle),
            "max_labelled_outcomes_from_one_vehicle": max_one_vehicle,
            "top_vehicle_share_of_labelled_outcomes": top_vehicle_share,
        },
        "decision_basis": (
            "Target evaluation remains blocked until Aura has a real multi-vehicle, "
            "time-separated cohort with observable 90-day outcomes, known censoring, "
            "and enough recurrence/non-recurrence volume for defensible baseline and "
            "calibration analysis."
        ),
        "next_gate": "continue_real_longitudinal_collection_and_rerun_target_audit",
    }


def _empty_episode_summary() -> dict[str, Any]:
    return {
        "resolved_episodes_total": 0,
        "distinct_vehicles_with_resolved_episodes": 0,
        "completed_90_day_windows": 0,
        "positive_recurrence": 0,
        "negative_observed": 0,
        "labelled_outcomes_total": 0,
        "censored_total": 0,
        "censoring": {},
        "recurrence_prevalence_among_observed": None,
        "distinct_vehicles_with_labelled_outcomes": 0,
        "max_labelled_outcomes_from_one_vehicle": 0,
        "top_vehicle_share_of_labelled_outcomes": None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    episodes = report["episodes"]
    lines = [
        "# Aura Wave 1.5 — 90-Day Reported Concern Recurrence Readiness",
        "",
        f"Target: `{report['target_id']}`",
        f"As of: `{report['as_of']}`",
        f"Database: `{report['database_backend']}`",
        f"Recommendation: **{report['recommendation']}**",
        f"Predictive implementation approved: **{report['predictive_implementation_approved']}**",
        "",
        "## Episode coverage",
        "",
        f"- Resolved episodes: `{episodes['resolved_episodes_total']}`",
        f"- Distinct vehicles with resolved episodes: `{episodes['distinct_vehicles_with_resolved_episodes']}`",
        f"- Completed 90-day windows: `{episodes['completed_90_day_windows']}`",
        f"- Positive recurrence outcomes: `{episodes['positive_recurrence']}`",
        f"- Observed non-recurrence outcomes: `{episodes['negative_observed']}`",
        f"- Labelled outcomes: `{episodes['labelled_outcomes_total']}`",
        f"- Censored outcomes: `{episodes['censored_total']}`",
        f"- Observed recurrence prevalence: `{episodes['recurrence_prevalence_among_observed']}`",
        "",
        "## Censoring",
        "",
    ]
    if episodes["censoring"]:
        lines.extend(
            f"- `{reason}`: {count}"
            for reason, count in episodes["censoring"].items()
        )
    else:
        lines.append("- none observed")

    lines.extend(["", "## Current constraints", ""])
    if report.get("constraints"):
        lines.extend(f"- `{item}`" for item in report["constraints"])
    else:
        lines.append("- none detected by this inventory; human readiness review still required")

    lines.extend(
        [
            "",
            "## Privacy boundary",
            "",
            "This report contains aggregates only. It omits vehicle IDs, concern IDs, "
            "user identifiers, free text and raw event payloads.",
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
    return _naive_utc(parsed)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--as-of",
        help="Optional ISO-8601 audit cutoff for deterministic review/testing.",
    )
    parser.add_argument("--output", help="Optional output file. Defaults to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from app import app  # noqa: PLC0415
    from extensions import db  # noqa: PLC0415

    with app.app_context(), open_read_only_connection(db.engine) as connection:
        report = build_recurrence_readiness_report(
            connection,
            as_of=_parse_as_of(args.as_of),
        )

    rendered = (
        json.dumps(report, indent=2, sort_keys=True)
        if args.format == "json"
        else render_markdown(report)
    )

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
