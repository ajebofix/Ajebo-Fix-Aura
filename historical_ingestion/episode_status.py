"""Compact historical episode status summaries for advisor UI surfaces."""

from __future__ import annotations

from typing import Any

from historical_ingestion.case_attribution import (
    attribution_payload,
    episodes_for_car,
    latest_case_attribution,
)
from historical_ingestion.reconciliation import (
    applied_reconciliation_plan,
    latest_reconciliation,
    reconciliation_signal,
)
from historical_ingestion.service import HistoricalSourceSummary


_STATE_PRIORITY = {
    "none": 0,
    "reconciled": 1,
    "preparing": 2,
    "advisor_review": 3,
    "ready_to_apply": 4,
    "needs_attention": 5,
    "reconciliation_needed": 6,
}


def historical_episode_status_views(
    *,
    car_id: int,
    source_summaries: list[HistoricalSourceSummary],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return episode-level status independently from reusable source-review state."""

    whatsapp_sources = [
        item.evidence
        for item in source_summaries
        if item.evidence.historical_source_type == "whatsapp_conversation"
        and item.evidence.evidence_type == "archive"
    ]

    views: list[dict[str, Any]] = []
    episodes = episodes_for_car(car_id)
    if limit is not None:
        episodes = episodes[: max(1, int(limit))]

    for episode in episodes:
        state = "none"
        attribution_extraction_id = None
        reconciliation_extraction_id = None

        for source in whatsapp_sources:
            analysis = latest_case_attribution(
                episode_id=episode.id,
                corpus_evidence_id=source.id,
            )
            if analysis is None or analysis.status != "completed":
                continue

            payload = attribution_payload(analysis)
            if not reconciliation_signal(payload):
                continue

            attribution_extraction_id = analysis.id
            reconciliation = latest_reconciliation(
                episode_id=episode.id,
                attribution_extraction_id=analysis.id,
            )

            candidate = "reconciliation_needed"
            if reconciliation is not None:
                reconciliation_extraction_id = reconciliation.id
                if reconciliation.status == "processing":
                    candidate = "preparing"
                elif reconciliation.status == "failed":
                    candidate = "needs_attention"
                elif applied_reconciliation_plan(reconciliation.id) is not None:
                    candidate = "reconciled"
                elif reconciliation.review_status in {"accepted", "corrected"}:
                    candidate = "ready_to_apply"
                elif reconciliation.status == "completed":
                    candidate = "advisor_review"

            if _STATE_PRIORITY[candidate] > _STATE_PRIORITY[state]:
                state = candidate

        views.append(
            {
                "episode": episode,
                "state": state,
                "attribution_extraction_id": attribution_extraction_id,
                "reconciliation_extraction_id": reconciliation_extraction_id,
            }
        )

    return views
