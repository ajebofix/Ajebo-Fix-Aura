from __future__ import annotations

from datetime import datetime
import json

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    text,
)
from sqlalchemy.exc import OperationalError

from scripts.audit_predictive_readiness import open_read_only_connection
from scripts.audit_wave_2_5_readiness import (
    build_wave_2_5_readiness_report,
    render_markdown,
)


@pytest.fixture()
def wave_2_5_engine():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    ownership = Table(
        "car_ownership",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("car_id", Integer, nullable=False),
        Column("start_date", DateTime),
        Column("end_date", DateTime),
        Column("is_active", Boolean, nullable=False),
    )
    events = Table(
        "vehicle_events",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("car_id", Integer, nullable=False),
        Column("ownership_id", Integer),
        Column("event_type", String(80), nullable=False),
        Column("subject_type", String(64), nullable=False),
        Column("subject_id", Integer, nullable=False),
        Column("occurred_at", DateTime, nullable=False),
        Column("schema_version", Integer, nullable=False),
        Column("source", String(80)),
        Column("progression_direction", String(40), nullable=False),
        Column("fingerprint", String(64), nullable=False),
        Column("visibility", String(24)),
        Column("actor_type", String(24)),
        Column("actor_authority", String(32)),
        Column("correction_of_event_id", Integer),
        Column("data", Text),
    )
    metadata.create_all(engine)

    car_a = 987654321
    car_b = 876543219
    with engine.begin() as connection:
        connection.execute(
            insert(ownership),
            [
                {
                    "id": 70001,
                    "car_id": car_a,
                    "start_date": datetime(2025, 1, 1),
                    "end_date": None,
                    "is_active": True,
                },
                {
                    "id": 70002,
                    "car_id": car_b,
                    "start_date": datetime(2025, 1, 1),
                    "end_date": None,
                    "is_active": True,
                },
            ],
        )

        raw_rows = [
            (1, car_a, 70001, "concern.resolved", "reported_concern", 910001, datetime(2026, 1, 1), "resolved"),
            (2, car_a, 70001, "concern.reopened", "reported_concern", 910001, datetime(2026, 1, 20), "reopened"),
            (3, car_b, 70002, "concern.resolved", "reported_concern", 910002, datetime(2026, 1, 2), "resolved"),
            (4, car_a, 70001, "consultation.completed", "consultation", 920001, datetime(2026, 2, 1), "not_applicable"),
            (5, car_a, 70001, "assessment.finalized", "vehicle_assessment", 930001, datetime(2026, 2, 2), "insufficient_evidence"),
            (6, car_a, 70001, "treatment.completed", "treatment_plan", 940001, datetime(2026, 2, 10), "improved"),
            (7, car_a, 70001, "treatment_action.completed", "treatment_action", 950001, datetime(2026, 2, 9), "not_applicable"),
            (8, car_b, 70002, "driver_observation.checkin_recorded", "driver_checkin", 960001, datetime(2026, 3, 1), "insufficient_evidence"),
            (9, car_b, 70002, "care_signal.resolved", "vehicle_health_alert", 970001, datetime(2026, 3, 2), "not_applicable"),
            (10, car_b, 70002, "priority.resolved", "priority_request", 980001, datetime(2026, 3, 3), "not_applicable"),
            (11, car_a, 70001, "evidence.reviewed", "vehicle_evidence", 990001, datetime(2026, 1, 15), "not_applicable"),
            (12, car_a, 70001, "treatment.outcome_recorded", "treatment_plan", 940001, datetime(2026, 2, 20), "improved"),
        ]

        rows = []
        for (
            row_id,
            car_id,
            ownership_id,
            event_type,
            subject_type,
            subject_id,
            occurred_at,
            direction,
        ) in raw_rows:
            rows.append(
                {
                    "id": row_id,
                    "car_id": car_id,
                    "ownership_id": ownership_id,
                    "event_type": event_type,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "occurred_at": occurred_at,
                    "schema_version": 1,
                    "source": "canonical_test_service",
                    "progression_direction": direction,
                    "fingerprint": f"{row_id:064x}",
                    "visibility": "advisor",
                    "actor_type": "user",
                    "actor_authority": "advisor",
                    "correction_of_event_id": None,
                    "data": "PRIVATE ROW PAYLOAD MUST NEVER APPEAR",
                }
            )
        connection.execute(insert(events), rows)

    return engine


def _report(engine):
    with open_read_only_connection(engine) as connection:
        return build_wave_2_5_readiness_report(
            connection,
            as_of=datetime(2026, 9, 10),
        )


def test_wave_2_5_report_recognises_current_taxonomy_and_target_gate(wave_2_5_engine):
    report = _report(wave_2_5_engine)
    ledger = report["canonical_ledger"]
    coverage = ledger["current_family_coverage"]
    episodes = report["recurrence_target"]["episodes"]

    assert coverage == {
        "covered": 9,
        "expected": 9,
        "ratio": 1.0,
        "missing": [],
    }
    for family in (
        "driver_observation",
        "care_signal",
        "priority",
        "treatment_action",
    ):
        assert ledger["families"][family]["event_count"] == 1

    assert all(
        item["subject_contract_mismatches"] == 0
        for item in ledger["families"].values()
        if item["expected_subject_type"] is not None
    )
    assert episodes["positive_recurrence"] == 1
    assert episodes["negative_observed"] == 1
    assert episodes["distinct_vehicles_with_labelled_outcomes"] == 2
    assert report["integrity_constraints"] == []
    assert report["target_constraints"] == []
    assert report["decision"] == "proceed_to_rules_baseline"
    assert report["rules_baseline_evaluation_permitted"] is True
    assert report["predictive_implementation_approved"] is False


def test_missing_current_family_is_reported_but_not_fabricated(wave_2_5_engine):
    with wave_2_5_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM vehicle_events WHERE event_type = 'priority.resolved'")
        )

    report = _report(wave_2_5_engine)
    coverage = report["canonical_ledger"]["current_family_coverage"]
    assert coverage["covered"] == 8
    assert coverage["missing"] == ["priority"]
    assert "current_canonical_family_coverage_is_incomplete" in report["advisory_notes"]
    assert report["decision"] == "proceed_to_rules_baseline"


def test_required_field_missingness_defers_rules_baseline(wave_2_5_engine):
    with wave_2_5_engine.begin() as connection:
        connection.execute(text("UPDATE vehicle_events SET source = NULL WHERE id = 1"))

    report = _report(wave_2_5_engine)
    assert report["decision"] == "defer"
    assert report["rules_baseline_evaluation_permitted"] is False
    assert "canonical_event_required_field_missingness_detected" in report[
        "integrity_constraints"
    ]


def test_wave_2_5_report_is_aggregate_and_omits_row_identity_and_payload(wave_2_5_engine):
    report = _report(wave_2_5_engine)
    serialized = json.dumps(report, sort_keys=True)

    # Aggregate field names such as `car_id`/`subject_id` may appear in the
    # missingness schema, but no row-level identifier values or raw payloads may.
    assert "987654321" not in serialized
    assert "876543219" not in serialized
    assert "910001" not in serialized
    assert "PRIVATE ROW PAYLOAD MUST NEVER APPEAR" not in serialized

    assert report["privacy"]["aggregate_only"] is True
    assert report["privacy"]["vehicle_ids_included"] is False
    assert report["privacy"]["subject_ids_included"] is False
    assert report["privacy"]["user_identifiers_included"] is False
    assert report["privacy"]["raw_event_payloads_included"] is False


def test_wave_2_5_audit_does_not_mutate_source_data(wave_2_5_engine):
    with open_read_only_connection(wave_2_5_engine) as connection:
        before = connection.execute(text("SELECT COUNT(*) FROM vehicle_events")).scalar()
        build_wave_2_5_readiness_report(connection, as_of=datetime(2026, 9, 10))
        with pytest.raises(OperationalError):
            connection.execute(
                text(
                    "INSERT INTO vehicle_events "
                    "(id, car_id, event_type, subject_type, subject_id, occurred_at, "
                    "schema_version, source, progression_direction, fingerprint) "
                    "VALUES (999, 1, 'concern.resolved', 'reported_concern', 1, "
                    "'2026-01-01', 1, 'forbidden', 'resolved', 'forbidden')"
                )
            )

    with wave_2_5_engine.connect() as connection:
        after = connection.execute(text("SELECT COUNT(*) FROM vehicle_events")).scalar()
    assert before == after == 12


def test_wave_2_5_markdown_keeps_decision_boundary_explicit(wave_2_5_engine):
    rendered = render_markdown(_report(wave_2_5_engine))
    assert "Longitudinal Coverage and Data-Quality Gate" in rendered
    assert "Decision: **proceed_to_rules_baseline**" in rendered
    assert "Predictive implementation approved: **False**" in rendered
    assert "Rules-baseline evaluation permitted: **True**" in rendered
    assert "PRIVATE" not in rendered
