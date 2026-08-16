"""Deterministic deployment readiness checks for Wave 1.4 evidence.

These checks are intentionally configuration-only. They do not contact private
object storage and do not read evidence records. Their job is to prevent a
production cutover from enabling evidence capabilities in an unsafe order or
with incomplete private-storage policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from evidence.storage import (
    EvidenceStorageConfigurationError,
    build_evidence_storage_provider,
)


_MAX_RETRIEVAL_GRANT_SECONDS = 300

_FEATURES = (
    ("image_intake", "EVIDENCE_IMAGE_INTAKE_ENABLED"),
    ("retrieval", "EVIDENCE_RETRIEVAL_ENABLED"),
    ("advisor_review", "EVIDENCE_ADVISOR_REVIEW_ENABLED"),
    ("timeline", "EVIDENCE_TIMELINE_ENABLED"),
    ("advisor_deletion", "EVIDENCE_ADVISOR_DELETION_ENABLED"),
)

_STORAGE_DEPENDENT_FLAGS = (
    "EVIDENCE_IMAGE_INTAKE_ENABLED",
    "EVIDENCE_RETRIEVAL_ENABLED",
    "EVIDENCE_ADVISOR_DELETION_ENABLED",
)


class EvidenceCutoverConfigurationError(RuntimeError):
    """Raised when enabled evidence capabilities are not safe to activate."""


@dataclass(frozen=True)
class EvidenceCutoverReadiness:
    """Safe, serializable summary of evidence cutover configuration."""

    enabled_features: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    storage_required: bool

    @property
    def ready(self) -> bool:
        return not self.errors

    @property
    def state(self) -> str:
        if not self.enabled_features:
            return "disabled"
        return "ready" if self.ready else "not_ready"

    def to_public_dict(self) -> dict[str, object]:
        """Return a health-check-safe summary with no credentials or bucket data."""

        return {
            "state": self.state,
            "enabled_features": list(self.enabled_features),
            "storage_required": self.storage_required,
            "issues": list(self.errors),
            "warnings": list(self.warnings),
        }


def _enabled(config: Mapping[str, object], key: str) -> bool:
    return bool(config.get(key, False))


def _positive_integer(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def evaluate_evidence_cutover_readiness(
    config: Mapping[str, object],
) -> EvidenceCutoverReadiness:
    """Evaluate evidence feature dependencies without contacting external services."""

    enabled_features = tuple(
        public_name
        for public_name, config_key in _FEATURES
        if _enabled(config, config_key)
    )
    errors: list[str] = []
    warnings: list[str] = []

    storage_required = any(_enabled(config, key) for key in _STORAGE_DEPENDENT_FLAGS)

    if storage_required:
        try:
            build_evidence_storage_provider(config)
        except EvidenceStorageConfigurationError:
            errors.append("private_storage_not_configured")

    if _enabled(config, "EVIDENCE_IMAGE_INTAKE_ENABLED"):
        if _positive_integer(config.get("EVIDENCE_RETENTION_DAYS")) is None:
            errors.append("retention_policy_not_configured")

    if _enabled(config, "EVIDENCE_RETRIEVAL_ENABLED"):
        grant_seconds = _positive_integer(
            config.get("EVIDENCE_RETRIEVAL_GRANT_SECONDS")
        )
        if grant_seconds is None or grant_seconds > _MAX_RETRIEVAL_GRANT_SECONDS:
            errors.append("retrieval_grant_policy_not_configured")

    if _enabled(config, "EVIDENCE_ADVISOR_REVIEW_ENABLED") and not _enabled(
        config,
        "EVIDENCE_RETRIEVAL_ENABLED",
    ):
        errors.append("advisor_review_requires_private_retrieval")

    if _enabled(config, "EVIDENCE_TIMELINE_ENABLED") and not _enabled(
        config,
        "EVIDENCE_ADVISOR_REVIEW_ENABLED",
    ):
        warnings.append("timeline_enabled_without_active_review")

    return EvidenceCutoverReadiness(
        enabled_features=enabled_features,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        storage_required=storage_required,
    )


def require_safe_evidence_cutover_configuration(
    config: Mapping[str, object],
) -> EvidenceCutoverReadiness:
    """Raise a generic deployment error when enabled evidence flags are unsafe."""

    readiness = evaluate_evidence_cutover_readiness(config)
    if not readiness.ready:
        raise EvidenceCutoverConfigurationError(
            "Evidence features are enabled with incomplete cutover configuration."
        )
    return readiness
