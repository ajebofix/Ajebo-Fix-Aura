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
    create_engine,
    insert,
    text,
)
from sqlalchemy.exc import OperationalError

from scripts.audit_predictive_readiness import open_read_only_connection
from scripts.audit_recurrence_target_readiness import (
    build_recurrence_readiness_report,
    render_markdown,
)


@pytest.fixture()
def recurrence_engine():
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
        Column("event_type", String(50), nullable=False),
        Column("subject_type", String(64)),
        Column("subject_id", Integer),
        Column("occurred_at", DateTime),
        Column("correction_of_event_id", Integer),
        Column("description", String(255)),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            insert(ownership),
            [
                {"id": 11, "car_id": 101, "start_date": datetime(2025, 1, 1), "end_date": None, "is_active": True},
                {"id": 12, "car_id": 102, "start_date": datetime(2025, 1, 1), "end_date": None, "is_active": True},
                {"id": 13, "car_id": 103, "start_date": datetime(2025, 1, 1), "end_date": None, "is_active": True},
                {"id": 14, "car_id": 104, "start_date": datetime(2025, 1, 1), "end_date": datetime(2026, 1, 30), "is_active": False},
                {"id": 15, "car_id": 105, "start_date": datetime(2025, 1, 1), "end_date": None, "is_active": True},
            ],
        )
        connection.execute(
            insert(events),
            [
                # Positive recurrence on car 101.
                {"id": 1, "car_id": 101, "ownership_id": 11, "event_type": "concern.resolved", "subject_type": "reported_concern", "subject_id": 1001, "occurred_at": datetime(2026, 1, 1), "correction_of_event_id": None, "description": "PRIVATE POSITIVE TEXT"},
                {"id": 2, "car_id": 101, "ownership_id": 11, "event_type": "concern.reopened", "subject_type": "reported_concern", "subject_id": 1001, "occurred_at": datetime(2026, 1, 20), "correction_of_event_id": None, "description": "PRIVATE REOPEN TEXT"},
                # Mature observed non-recurrence on car 102.
                {"id": 3, "car_id": 102, "ownership_id": 12, "event_type": "concern.resolved", "subject_type": "reported_concern", "subject_id": 1002, "occurred_at": datetime(2026, 1, 1), "correction_of_event_id": None, "description": "PRIVATE NEGATIVE TEXT"},
                # Insufficient follow-up on car 103.
                {"id": 4, "car_id": 103, "ownership_id": 13, "event_type": "concern.resolved", "subject_type": "reported_concern", "subject_id": 1003, "occurred_at": datetime(2026, 4, 15), "correction_of_event_id": None, "description": "PRIVATE RECENT TEXT"},
                # Monitoring ended before the 90-day horizon on car 104.
                {"id": 5, "car_id": 104, "ownership_id": 14, "event_type": "concern.resolved", "subject_type": "reported_concern", "subject_id": 1004, "occurred_at": datetime(2026, 1, 1), "correction_of_event_id": None, "description": "PRIVATE ENDED TEXT"},
                # Corrected resolved episode on car 105.
                {"id": 6, "car_id": 105, "ownership_id": 15, "event_type": "concern.resolved", "subject_type": "reported_concern", "subject_id": 1005, "occurred_at": datetime(2026, 1, 1), "correction_of_event_id": None, "description": "PRIVATE CORRECTED TEXT"},
                {"id": 7, "car_id": 105, "ownership_id": 15, "event_type": "concern.corrected", "subject_type": "reported_concern", "subject_id": 1005, "occurred_at": datetime(2026, 1, 10), "correction_of_event_id": 6, "description": "PRIVATE CORRECTION TEXT"},
            ],
        )

    return engine


def test_recurrence_report_classifies_positive_negative_and_censoring(recurrence_engine):
    as_of = datetime(2026, 6, 1)
    with open_read_only_connection(recurrence_engine) as connection:
        report = build_recurrence_readiness_report(connection, as_of=as_of)

    episodes = report["episodes"]
    assert report["recommendation"] == "collect_more_data"
    assert report["predictive_implementation_approved"] is False
    assert episodes["resolved_episodes_total"] == 5
    assert episodes["distinct_vehicles_with_resolved_episodes"] == 5
    assert episodes["positive_recurrence"] == 1
    assert episodes["negative_observed"] == 1
    assert episodes["labelled_outcomes_total"] == 2
    assert episodes["censored_total"] == 3
    assert episodes["censoring"] == {
        "censored_corrected": 1,
        "censored_insufficient_followup": 1,
        "censored_monitoring_ended": 1,
    }
    assert episodes["recurrence_prevalence_among_observed"] == 0.5
    assert episodes["distinct_vehicles_with_labelled_outcomes"] == 2
    assert episodes["max_labelled_outcomes_from_one_vehicle"] == 1
    assert episodes["top_vehicle_share_of_labelled_outcomes"] == 0.5


def test_positive_outcome_can_be_known_before_full_90_day_window(recurrence_engine):
    as_of = datetime(2026, 2, 1)
    with open_read_only_connection(recurrence_engine) as connection:
        report = build_recurrence_readiness_report(connection, as_of=as_of)

    episodes = report["episodes"]
    assert episodes["positive_recurrence"] == 1
    assert episodes["negative_observed"] == 0
    assert episodes["completed_90_day_windows"] == 0
    assert episodes["censoring"]["censored_insufficient_followup"] >= 1


def test_report_is_aggregate_and_omits_private_row_values(recurrence_engine):
    with open_read_only_connection(recurrence_engine) as connection:
        report = build_recurrence_readiness_report(
            connection,
            as_of=datetime(2026, 6, 1),
        )

    serialized = json.dumps(report, sort_keys=True)
    assert "PRIVATE POSITIVE TEXT" not in serialized
    assert "PRIVATE REOPEN TEXT" not in serialized
    assert "1001" not in serialized
    assert "101" not in serialized

    forbidden_keys = {
        "car_id",
        "subject_id",
        "concern_id",
        "ownership_id",
        "user_id",
        "description",
        "data",
        "event_id",
    }

    def walk(value):
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value.keys())
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(report)


def test_shared_read_only_transaction_still_rejects_writes(recurrence_engine):
    with open_read_only_connection(recurrence_engine) as connection:
        build_recurrence_readiness_report(connection, as_of=datetime(2026, 6, 1))
        with pytest.raises(OperationalError):
            connection.execute(
                text(
                    "INSERT INTO vehicle_events "
                    "(id, car_id, event_type, subject_type, subject_id, occurred_at) "
                    "VALUES (999, 999, 'concern.resolved', 'reported_concern', 999, "
                    "'2026-01-01 00:00:00')"
                )
            )

    with recurrence_engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM vehicle_events")).scalar()
    assert count == 7


def test_markdown_is_target_specific_and_private(recurrence_engine):
    with open_read_only_connection(recurrence_engine) as connection:
        report = build_recurrence_readiness_report(
            connection,
            as_of=datetime(2026, 6, 1),
        )

    rendered = render_markdown(report)
    assert "90-Day Reported Concern Recurrence Readiness" in rendered
    assert "Positive recurrence outcomes: `1`" in rendered
    assert "Observed non-recurrence outcomes: `1`" in rendered
    assert "vehicle IDs, concern IDs" in rendered
    assert "PRIVATE" not in rendered
