from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from extensions import db
from maintenance.knowledge_service import (
    MaintenanceKnowledgeAuthorityError,
    MaintenanceKnowledgeError,
    MaintenanceKnowledgeService,
    MaintenanceKnowledgeStateError,
)
from maintenance.models import MaintenanceKnowledgeRule
from models import User


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Maintenance {role} {suffix}",
        email=f"maintenance-{role}-{suffix}@example.com",
        phone_number=f"+2348552{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _candidate(**overrides) -> MaintenanceKnowledgeRule:
    payload = {
        "maintenance_item_key": "engine_oil",
        "display_name": "Engine oil service",
        "interval_km": 10000,
        "interval_months": 12,
        "source_type": "oem",
        "source_name": "Verified manufacturer schedule reference",
        "source_reference": "reference://maintenance/engine-oil/v1",
        "source_version": "v1",
        "brand": "Mercedes-Benz",
        "model": "GLE 450",
        "year_start": 2021,
        "year_end": 2021,
    }
    payload.update(overrides)
    return MaintenanceKnowledgeService.create_candidate(**payload)


def test_candidate_is_unverified_non_active_and_idempotent(app):
    with app.app_context():
        first = _candidate()
        replay = _candidate()
        db.session.commit()

        assert first.id == replay.id
        assert first.verification_status == "unverified"
        assert first.is_production_active is False
        assert MaintenanceKnowledgeRule.query.count() == 1
        assert MaintenanceKnowledgeService.production_active_query().count() == 0


def test_candidate_requires_explicit_interval_applicability_and_provenance(app):
    with app.app_context():
        with pytest.raises(MaintenanceKnowledgeError):
            _candidate(interval_km=None, interval_months=None)

        with pytest.raises(MaintenanceKnowledgeError):
            _candidate(brand=None, model=None)

        with pytest.raises(MaintenanceKnowledgeError):
            _candidate(source_reference="")

        with pytest.raises(MaintenanceKnowledgeError):
            _candidate(maintenance_item_key="Engine Oil")


def test_only_advisor_can_verify_and_advisor_verified_becomes_active(app):
    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        rule = _candidate()

        with pytest.raises(MaintenanceKnowledgeAuthorityError):
            MaintenanceKnowledgeService.verify(
                rule_id=rule.id,
                actor_user_id=owner.id,
            )

        verified = MaintenanceKnowledgeService.verify(
            rule_id=rule.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 9, 12, 12, 0, 0),
        )
        db.session.commit()

        assert verified.verification_status == "advisor_verified"
        assert verified.verified_by == advisor.id
        assert verified.verified_at == datetime(2026, 9, 12, 12, 0, 0)
        assert verified.is_production_active is True
        assert MaintenanceKnowledgeService.production_active_query().one().id == rule.id


def test_source_verified_is_reviewed_but_not_production_active(app):
    with app.app_context():
        advisor = _user(suffix=3, role="admin")
        rule = _candidate(source_reference="reference://maintenance/engine-oil/source-v")

        MaintenanceKnowledgeService.verify(
            rule_id=rule.id,
            actor_user_id=advisor.id,
            verification_status="source_verified",
        )
        db.session.commit()

        assert rule.verification_status == "source_verified"
        assert rule.is_production_active is False
        assert MaintenanceKnowledgeService.production_active_query().count() == 0


def test_test_source_can_never_be_verified_for_production(app):
    with app.app_context():
        advisor = _user(suffix=4, role="admin")
        rule = _candidate(
            source_type="test",
            source_name="MockMaintenanceProvider",
            source_reference="mock://service-a",
        )

        with pytest.raises(MaintenanceKnowledgeStateError):
            MaintenanceKnowledgeService.verify(
                rule_id=rule.id,
                actor_user_id=advisor.id,
            )
        db.session.rollback()

        assert MaintenanceKnowledgeService.production_active_query().count() == 0


def test_rejection_is_terminal_and_removes_production_eligibility(app):
    with app.app_context():
        advisor = _user(suffix=5, role="admin")
        rule = _candidate(source_reference="reference://maintenance/engine-oil/reject")
        MaintenanceKnowledgeService.verify(
            rule_id=rule.id,
            actor_user_id=advisor.id,
        )
        MaintenanceKnowledgeService.reject(
            rule_id=rule.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        assert rule.verification_status == "rejected"
        assert rule.is_production_active is False

        with pytest.raises(MaintenanceKnowledgeStateError):
            MaintenanceKnowledgeService.verify(
                rule_id=rule.id,
                actor_user_id=advisor.id,
            )


def test_supersession_preserves_old_rule_and_requires_verified_replacement(app):
    with app.app_context():
        advisor = _user(suffix=6, role="admin")
        old = _candidate(source_reference="reference://maintenance/engine-oil/old")
        replacement = _candidate(
            interval_km=12000,
            source_reference="reference://maintenance/engine-oil/new",
            source_version="v2",
        )
        MaintenanceKnowledgeService.verify(
            rule_id=old.id,
            actor_user_id=advisor.id,
        )

        with pytest.raises(MaintenanceKnowledgeStateError):
            MaintenanceKnowledgeService.supersede(
                rule_id=old.id,
                replacement_rule_id=replacement.id,
                actor_user_id=advisor.id,
            )

        MaintenanceKnowledgeService.verify(
            rule_id=replacement.id,
            actor_user_id=advisor.id,
        )
        MaintenanceKnowledgeService.supersede(
            rule_id=old.id,
            replacement_rule_id=replacement.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        assert old.verification_status == "superseded"
        assert old.superseded_by_id == replacement.id
        assert old.is_production_active is False
        assert replacement.is_production_active is True
        assert MaintenanceKnowledgeRule.query.count() == 2


def test_database_rejects_verified_status_without_review_metadata(app):
    with app.app_context():
        rule = MaintenanceKnowledgeRule(
            maintenance_item_key="brake_fluid",
            display_name="Brake fluid",
            brand="Mercedes-Benz",
            interval_months=24,
            source_type="oem",
            source_name="Manufacturer reference",
            source_reference="reference://maintenance/brake-fluid",
            verification_status="advisor_verified",
            fingerprint="a" * 64,
        )
        db.session.add(rule)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_service_does_not_commit_implicitly(app):
    with app.app_context():
        _candidate(source_reference="reference://maintenance/engine-oil/rollback")
        db.session.rollback()
        assert MaintenanceKnowledgeRule.query.count() == 0
