"""Mutation authority for Aura maintenance knowledge.

The service deliberately never commits. Candidate ingestion, professional review,
and supersession stay in one caller-owned transaction boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from extensions import db
from maintenance.models import (
    MAINTENANCE_SOURCE_TYPES,
    MaintenanceKnowledgeRule,
)
from models import User


_ITEM_KEY_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_REVIEWABLE_STATUSES = frozenset({"unverified", "source_verified", "advisor_verified"})
_VERIFIABLE_TARGETS = frozenset({"source_verified", "advisor_verified"})
_TERMINAL_STATUSES = frozenset({"superseded", "rejected"})


class MaintenanceKnowledgeError(ValueError):
    """Base error for maintenance knowledge operations."""


class MaintenanceKnowledgeAuthorityError(MaintenanceKnowledgeError):
    """Raised when a non-advisor attempts a professional knowledge decision."""


class MaintenanceKnowledgeStateError(MaintenanceKnowledgeError):
    """Raised when a knowledge status transition is not allowed."""


class MaintenanceKnowledgeConflict(MaintenanceKnowledgeError):
    """Raised when a candidate conflicts with locked idempotency semantics."""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_text(
    value: str | None,
    *,
    field: str,
    limit: int,
    required: bool = False,
) -> str | None:
    clean = (value or "").strip()
    if not clean:
        if required:
            raise MaintenanceKnowledgeError(f"{field} is required")
        return None
    if len(clean) > limit:
        raise MaintenanceKnowledgeError(f"{field} must be {limit} characters or fewer")
    return clean


def _normalise_item_key(value: str) -> str:
    key = (value or "").strip().lower()
    if not key or len(key) > 100 or not _ITEM_KEY_RE.fullmatch(key):
        raise MaintenanceKnowledgeError(
            "maintenance_item_key must be lowercase snake_case and 100 characters or fewer"
        )
    return key


def _positive_int(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MaintenanceKnowledgeError(f"{field} must be a positive integer")
    return value


def _optional_year(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1886 or value > 2200:
        raise MaintenanceKnowledgeError(f"{field} must be a plausible vehicle year")
    return value


def _require_advisor(actor_user_id: int) -> User:
    if not isinstance(actor_user_id, int):
        raise MaintenanceKnowledgeAuthorityError("advisor review requires a user id")
    actor = db.session.get(User, actor_user_id)
    if actor is None or not actor.is_admin or not actor.is_active:
        raise MaintenanceKnowledgeAuthorityError(
            "maintenance knowledge verification requires advisor authority"
        )
    return actor


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MaintenanceKnowledgeService:
    """Single service boundary for maintenance knowledge lifecycle decisions."""

    @staticmethod
    def create_candidate(
        *,
        maintenance_item_key: str,
        display_name: str,
        interval_km: int | None,
        interval_months: int | None,
        source_type: str,
        source_name: str,
        source_reference: str,
        source_version: str | None = None,
        car_id: int | None = None,
        brand: str | None = None,
        model: str | None = None,
        year_start: int | None = None,
        year_end: int | None = None,
        trim: str | None = None,
        engine_type: str | None = None,
        fuel_type: str | None = None,
        transmission: str | None = None,
        drive_type: str | None = None,
    ) -> MaintenanceKnowledgeRule:
        """Persist an unverified candidate without promoting it to production truth."""

        item_key = _normalise_item_key(maintenance_item_key)
        display_name = _clean_text(
            display_name,
            field="display_name",
            limit=150,
            required=True,
        )
        interval_km = _positive_int(interval_km, field="interval_km")
        interval_months = _positive_int(interval_months, field="interval_months")
        if interval_km is None and interval_months is None:
            raise MaintenanceKnowledgeError(
                "at least one maintenance interval dimension is required"
            )

        source_type = (source_type or "").strip().lower()
        if source_type not in MAINTENANCE_SOURCE_TYPES:
            raise MaintenanceKnowledgeError("unsupported maintenance source_type")
        source_name = _clean_text(
            source_name,
            field="source_name",
            limit=150,
            required=True,
        )
        source_reference = _clean_text(
            source_reference,
            field="source_reference",
            limit=500,
            required=True,
        )
        source_version = _clean_text(
            source_version,
            field="source_version",
            limit=120,
        )

        if car_id is not None:
            from models import Car

            if db.session.get(Car, car_id) is None:
                raise MaintenanceKnowledgeError("vehicle override does not exist")

        brand = _clean_text(brand, field="brand", limit=100)
        model = _clean_text(model, field="model", limit=120)
        trim = _clean_text(trim, field="trim", limit=120)
        engine_type = _clean_text(engine_type, field="engine_type", limit=120)
        fuel_type = _clean_text(fuel_type, field="fuel_type", limit=80)
        transmission = _clean_text(transmission, field="transmission", limit=120)
        drive_type = _clean_text(drive_type, field="drive_type", limit=80)

        if car_id is None and brand is None:
            raise MaintenanceKnowledgeError(
                "maintenance knowledge requires a vehicle override or brand applicability"
            )

        year_start = _optional_year(year_start, field="year_start")
        year_end = _optional_year(year_end, field="year_end")
        if year_start is not None and year_end is not None and year_start > year_end:
            raise MaintenanceKnowledgeError("year_start cannot be after year_end")

        semantics = {
            "maintenance_item_key": item_key,
            "display_name": display_name,
            "car_id": car_id,
            "brand": brand,
            "model": model,
            "year_start": year_start,
            "year_end": year_end,
            "trim": trim,
            "engine_type": engine_type,
            "fuel_type": fuel_type,
            "transmission": transmission,
            "drive_type": drive_type,
            "interval_km": interval_km,
            "interval_months": interval_months,
            "source_type": source_type,
            "source_name": source_name,
            "source_reference": source_reference,
            "source_version": source_version,
        }
        fingerprint = _fingerprint(semantics)
        existing = MaintenanceKnowledgeRule.query.filter_by(fingerprint=fingerprint).first()
        if existing is not None:
            return existing

        rule = MaintenanceKnowledgeRule(
            **semantics,
            verification_status="unverified",
            fingerprint=fingerprint,
        )
        db.session.add(rule)
        db.session.flush()
        return rule

    @staticmethod
    def verify(
        *,
        rule_id: int,
        actor_user_id: int,
        verification_status: str = "advisor_verified",
        occurred_at: datetime | None = None,
    ) -> MaintenanceKnowledgeRule:
        """Professionally review a candidate without making provider output self-trusting."""

        _require_advisor(actor_user_id)
        target = (verification_status or "").strip().lower()
        if target not in _VERIFIABLE_TARGETS:
            raise MaintenanceKnowledgeStateError("invalid verification target")

        rule = db.session.get(MaintenanceKnowledgeRule, rule_id)
        if rule is None:
            raise MaintenanceKnowledgeError("maintenance knowledge rule does not exist")
        if rule.source_type == "test":
            raise MaintenanceKnowledgeStateError(
                "test maintenance knowledge cannot become verified production knowledge"
            )
        if rule.verification_status in _TERMINAL_STATUSES:
            raise MaintenanceKnowledgeStateError(
                "rejected or superseded maintenance knowledge is terminal"
            )
        if rule.verification_status == target:
            return rule
        if rule.verification_status == "advisor_verified" and target == "source_verified":
            raise MaintenanceKnowledgeStateError(
                "advisor-verified knowledge cannot be downgraded to source-verified"
            )

        rule.verification_status = target
        rule.verified_by = actor_user_id
        rule.verified_at = occurred_at or _utcnow_naive()
        db.session.flush()
        return rule

    @staticmethod
    def reject(
        *,
        rule_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
    ) -> MaintenanceKnowledgeRule:
        _require_advisor(actor_user_id)
        rule = db.session.get(MaintenanceKnowledgeRule, rule_id)
        if rule is None:
            raise MaintenanceKnowledgeError("maintenance knowledge rule does not exist")
        if rule.verification_status == "rejected":
            return rule
        if rule.verification_status == "superseded":
            raise MaintenanceKnowledgeStateError("superseded knowledge cannot be rejected")

        rule.verification_status = "rejected"
        rule.verified_by = actor_user_id
        rule.verified_at = occurred_at or _utcnow_naive()
        db.session.flush()
        return rule

    @staticmethod
    def supersede(
        *,
        rule_id: int,
        replacement_rule_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
    ) -> MaintenanceKnowledgeRule:
        _require_advisor(actor_user_id)
        if rule_id == replacement_rule_id:
            raise MaintenanceKnowledgeStateError("a rule cannot supersede itself")

        rule = db.session.get(MaintenanceKnowledgeRule, rule_id)
        replacement = db.session.get(MaintenanceKnowledgeRule, replacement_rule_id)
        if rule is None or replacement is None:
            raise MaintenanceKnowledgeError("maintenance knowledge rule does not exist")
        if rule.verification_status in _TERMINAL_STATUSES:
            if (
                rule.verification_status == "superseded"
                and rule.superseded_by_id == replacement.id
            ):
                return rule
            raise MaintenanceKnowledgeStateError(
                "terminal maintenance knowledge cannot be superseded again"
            )
        if not replacement.is_production_active:
            raise MaintenanceKnowledgeStateError(
                "replacement knowledge must be advisor-verified first"
            )
        if replacement.maintenance_item_key != rule.maintenance_item_key:
            raise MaintenanceKnowledgeConflict(
                "replacement knowledge must describe the same maintenance item"
            )

        rule.verification_status = "superseded"
        rule.superseded_by_id = replacement.id
        rule.verified_by = actor_user_id
        rule.verified_at = occurred_at or _utcnow_naive()
        db.session.flush()
        return rule

    @staticmethod
    def production_active_query():
        """Return the deliberately narrow production-eligible knowledge query."""

        return MaintenanceKnowledgeRule.query.filter(
            MaintenanceKnowledgeRule.verification_status == "advisor_verified",
            MaintenanceKnowledgeRule.source_type != "test",
        )
