from __future__ import annotations

from datetime import datetime, timedelta
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

from scripts.audit_predictive_readiness import (
    build_readiness_report,
    open_read_only_connection,
    render_markdown,
)


@pytest.fixture()
def readiness_engine():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    cars = Table(
        "cars",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("vin", String(50), nullable=False),
        Column("current_mileage", Integer),
        Column("vehicle_identity_source", String(20)),
    )
    events = Table(
        "vehicle_events",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("car_id", Integer, nullable=False),
        Column("event_type", String(50), nullable=False),
        Column("subject_type", String(40)),
        Column("subject_id", Integer),
        Column("occurred_at", DateTime),
        Column("schema_version", Integer),
        Column("source", String(50)),
        Column("progression_direction", String(32)),
        Column("fingerprint", String(64)),
        Column("visibility", String(20)),
    )
    concerns = Table(
        "car_faults",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("car_id", Integer, nullable=False),
        Column("status", String(32)),
        Column("description", String(255)),
    )
    evidence = Table(
        "vehicle_evidence",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("car_id", Integer, nullable=False),
        Column("review_status", String(24)),
        Column("storage_state", String(24)),
        Column("source_channel", String(24)),
        Column("object_key", String(255)),
        Column("sha256", String(64)),
    )
    recalls = Table(
        "vehicle_recalls",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("car_id", Integer, nullable=False),
        Column("is_open", Boolean),
        Column("source", String(100)),
    )

    metadata.create_all(engine)

    start = datetime(2026, 1, 1, 9, 0, 0)
    with engine.begin() as connection:
        connection.execute(
            insert(cars),
            [
                {
                    "id": 9001,
                    "vin": "PRIVATEVIN000000001",
                    "current_mileage": 10000,
                    "vehicle_identity_source": "vin",
                },
                {
                    "id": 9002,
                    "vin": "PRIVATEVIN000000002",
                    "current_mileage": 20000,
                    "vehicle_identity_source": "manual",
                },
            ],
        )
        connection.execute(
            insert(events),
            [
                {
                    "id": 1,
                    "car_id": 9001,
                    "event_type": "concern.reported",
                    "subject_type": "reported_concern",
                    "subject_id": 501,
                    "occurred_at": start,
                    "schema_version": 1,
                    "source": "concern_service",
                    "progression_direction": "insufficient_evidence",
                    "fingerprint": "a" * 64,
                    "visibility": "client",
                },
                {
                    "id": 2,
                    "car_id": 9001,
                    "event_type": "concern.resolved",
                    "subject_type": "reported_concern",
                    "subject_id": 501,
                    "occurred_at": start + timedelta(days=10),
                    "schema_version": 1,
                    "source": "concern_service",
                    "progression_direction": "resolved",
                    "fingerprint": "b" * 64,
                    "visibility": "client",
                },
                {
                    "id": 3,
                    "car_id": 9001,
                    "event_type": "evidence.reviewed",
                    "subject_type": "vehicle_evidence",
                    "subject_id": 701,
                    "occurred_at": start + timedelta(days=3),
                    "schema_version": 1,
                    "source": "evidence_review",
                    "progression_direction": "not_applicable",
                    "fingerprint": "c" * 64,
                    "visibility": "client",
                },
                {
                    "id": 4,
                    "car_id": 9002,
                    "event_type": "concern.reported",
                    "subject_type": "reported_concern",
                    "subject_id": 502,
                    "occurred_at": start + timedelta(days=20),
                    "schema_version": 1,
                    "source": "concern_service",
                    "progression_direction": "insufficient_evidence",
                    "fingerprint": "d" * 64,
                    "visibility": "client",
                },
            ],
        )
        connection.execute(
            insert(concerns),
            [
                {
                    "id": 501,
                    "car_id": 9001,
                    "status": "resolved",
                    "description": "PRIVATE FREE TEXT SHOULD NEVER APPEAR",
                },
                {
                    "id": 502,
                    "car_id": 9002,
                    "status": "monitoring",
                    "description": "ANOTHER PRIVATE OBSERVATION",
                },
            ],
        )
        connection.execute(
            insert(evidence),
            [
                {
                    "id": 701,
                    "car_id": 9001,
                    "review_status": "accepted",
                    "storage_state": "available",
                    "source_channel": "web",
                    "object_key": "secret/private/object-key.png",
                    "sha256": "f" * 64,
                }
            ],
        )
        connection.execute(
            insert(recalls),
            [{"id": 801, "car_id": 9001, "is_open": True, "source": "NHTSA"}],
        )

    return engine


def test_readiness_report_is_aggregate_and_conservative(readiness_engine):
    with open_read_only_connection(readiness_engine) as connection:
        report = build_readiness_report(connection)

    assert report["recommendation"] == "collect_more_data"
    assert report["proceed_allowed"] is False
    assert report["table_counts"]["cars"] == 2
    assert report["table_counts"]["vehicle_events"] == 4
    assert report["canonical_events"]["families"] == {
        "concern": 3,
        "evidence": 1,
    }
    assert report["canonical_events"]["longitudinal"]["vehicles_with_2_plus_events"] == 1
    assert report["potential_outcome_signals"]["canonical_progression_directions"][
        "resolved"
    ] == 1
    assert "prediction_target_not_approved" in report["constraints"]
    assert "canonical_event_source_coverage_is_incomplete" in report["constraints"]


def test_read_only_connection_rejects_mutation(readiness_engine):
    with open_read_only_connection(readiness_engine) as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM cars")).scalar() == 2
        with pytest.raises(OperationalError):
            connection.execute(
                text(
                    "INSERT INTO cars (id, vin, current_mileage, vehicle_identity_source) "
                    "VALUES (9999, 'SHOULD-NOT-WRITE', 1, 'manual')"
                )
            )

    with readiness_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM cars")).scalar() == 2


def test_report_omits_row_level_private_values(readiness_engine):
    with open_read_only_connection(readiness_engine) as connection:
        report = build_readiness_report(connection)

    serialized = json.dumps(report, sort_keys=True)

    # Aggregate provenance may legitimately contain the category value "vin";
    # the privacy boundary prohibits actual VIN values and row-level VIN fields.
    assert report["provenance_distributions"]["cars"]["vehicle_identity_source"] == {
        "vin": 1,
        "manual": 1,
    }
    assert "PRIVATEVIN000000001" not in serialized
    assert "PRIVATEVIN000000002" not in serialized
    assert "PRIVATE FREE TEXT SHOULD NEVER APPEAR" not in serialized
    assert "ANOTHER PRIVATE OBSERVATION" not in serialized
    assert "secret/private/object-key.png" not in serialized
    assert "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff" not in serialized

    forbidden_structural_keys = {
        "car_id",
        "user_id",
        "uploaded_by_user_id",
        "reviewed_by_user_id",
        "email",
        "phone_number",
        "object_key",
        "sha256",
        "description",
        "data",
    }

    def walk(value):
        if isinstance(value, dict):
            assert forbidden_structural_keys.isdisjoint(value.keys())
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(report)


def test_markdown_contains_only_aggregate_readiness_summary(readiness_engine):
    with open_read_only_connection(readiness_engine) as connection:
        report = build_readiness_report(connection)

    rendered = render_markdown(report)

    assert "Recommendation: **collect_more_data**" in rendered
    assert "`concern.reported`: 2" in rendered
    assert "`evidence.reviewed`: 1" in rendered
    assert "vehicle IDs, VINs, user identifiers" in rendered
    assert "PRIVATEVIN" not in rendered
    assert "secret/private/object-key" not in rendered


def test_report_builder_does_not_change_source_rows(readiness_engine):
    with readiness_engine.connect() as connection:
        before = connection.execute(text("SELECT COUNT(*) FROM vehicle_events")).scalar()

    with open_read_only_connection(readiness_engine) as connection:
        build_readiness_report(connection)

    with readiness_engine.connect() as connection:
        after = connection.execute(text("SELECT COUNT(*) FROM vehicle_events")).scalar()

    assert before == after == 4
