"""Server-side continuation for governed historical-source analysis.

The browser may observe progress, but it must never be responsible for making
analysis progress. Production web workers run a small durable scanner that
resumes any in-flight historical extraction after client disconnects or process
restarts. Redis locks prevent two Gunicorn workers (or a status request and the
runner) from advancing the same extraction concurrently.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time
from typing import Iterator

from redis import Redis
from redis.exceptions import LockError, RedisError

from evidence.models import EvidenceExtraction
from extensions import db
from historical_ingestion.service import (
    advance_historical_background_analysis,
)
from historical_ingestion.case_attribution import (
    PIPELINE as CASE_ATTRIBUTION_PIPELINE,
    advance_case_attribution,
)
from historical_ingestion.whatsapp_bundle import (
    PIPELINE as WHATSAPP_PIPELINE,
    advance_whatsapp_bundle_analysis,
)


PDF_PIPELINE = "advisor_grade_background_v3"
_SUPPORTED_PIPELINES = {
    WHATSAPP_PIPELINE,
    PDF_PIPELINE,
    CASE_ATTRIBUTION_PIPELINE,
}

_RUNNER_START_LOCK = threading.Lock()
_RUNNER_STARTED_PIDS: set[int] = set()


def _runner_enabled(app) -> bool:
    configured = os.getenv("HISTORICAL_BACKGROUND_RUNNER_ENABLED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return str(app.config.get("APP_ENV") or "").lower() == "production"


def _redis_url(app) -> str:
    return str(
        os.getenv("REDIS_URL")
        or app.config.get("RATELIMIT_STORAGE_URI")
        or ""
    ).strip()


@contextmanager
def historical_analysis_lock(app, extraction_id: int) -> Iterator[bool]:
    """Hold a cross-process lock for one analysis transition."""

    redis_url = _redis_url(app)
    if not redis_url or redis_url.startswith("memory://"):
        # Development/tests keep their previous synchronous behaviour.
        yield True
        return

    client = Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )
    lock = client.lock(
        f"aura:historical-analysis:{int(extraction_id)}",
        timeout=15 * 60,
        blocking_timeout=0,
    )

    acquired = False
    try:
        acquired = bool(lock.acquire(blocking=False))
    except RedisError:
        app.logger.exception(
            "historical_analysis_lock_unavailable extraction_id=%s",
            extraction_id,
        )
        yield False
        return

    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except (LockError, RedisError):
                app.logger.warning(
                    "historical_analysis_lock_release_failed extraction_id=%s",
                    extraction_id,
                )


def advance_historical_analysis_once(app, extraction_id: int):
    """Advance one durable analysis transition if this process owns the lock."""

    with historical_analysis_lock(app, extraction_id) as acquired:
        if not acquired:
            return None

        analysis = db.session.get(EvidenceExtraction, extraction_id)
        if analysis is None or analysis.status != "processing":
            return None

        provenance = dict(analysis.provenance or {})
        pipeline = str(provenance.get("analysis_pipeline") or "")
        if pipeline not in _SUPPORTED_PIPELINES:
            return None

        try:
            actor_user_id = int(provenance.get("started_by_user_id") or 0)
        except (TypeError, ValueError):
            actor_user_id = 0
        if actor_user_id <= 0:
            app.logger.error(
                "historical_background_missing_actor extraction_id=%s pipeline=%s",
                extraction_id,
                pipeline,
            )
            return None

        if pipeline == WHATSAPP_PIPELINE:
            return advance_whatsapp_bundle_analysis(
                extraction_id=analysis.id,
                actor_user_id=actor_user_id,
                storage_provider=app.extensions.get("evidence_storage_provider"),
                storage_config=app.config,
            )

        if pipeline == CASE_ATTRIBUTION_PIPELINE:
            return advance_case_attribution(
                extraction_id=analysis.id,
                actor_user_id=actor_user_id,
            )

        return advance_historical_background_analysis(
            extraction_id=analysis.id,
            actor_user_id=actor_user_id,
            language_provider=app.extensions.get("historical_document_provider"),
        )


def _processing_extraction_ids() -> list[int]:
    rows = (
        EvidenceExtraction.query.filter(
            EvidenceExtraction.extraction_type.in_(
                {"structured_fields", "historical_case_attribution"}
            ),
            EvidenceExtraction.status == "processing",
        )
        .order_by(EvidenceExtraction.id.asc())
        .limit(30)
        .all()
    )
    return [
        row.id
        for row in rows
        if str((row.provenance or {}).get("analysis_pipeline") or "")
        in _SUPPORTED_PIPELINES
    ]


def _runner_loop(app) -> None:
    app.logger.warning(
        "historical_background_runner_started pid=%s",
        os.getpid(),
    )

    while True:
        ids: list[int] = []
        try:
            with app.app_context():
                ids = _processing_extraction_ids()
                db.session.remove()

            for extraction_id in ids:
                try:
                    with app.app_context():
                        state = advance_historical_analysis_once(
                            app,
                            extraction_id,
                        )
                        if state is not None:
                            app.logger.info(
                                "historical_background_runner_advanced "
                                "extraction_id=%s status=%s phase=%s",
                                extraction_id,
                                getattr(state, "status", "unknown"),
                                getattr(state, "phase", "unknown"),
                            )
                        db.session.remove()
                except Exception:
                    with app.app_context():
                        db.session.rollback()
                        db.session.remove()
                    app.logger.exception(
                        "historical_background_runner_step_failed extraction_id=%s",
                        extraction_id,
                    )
        except Exception:
            with app.app_context():
                db.session.rollback()
                db.session.remove()
            app.logger.exception("historical_background_runner_scan_failed")

        time.sleep(1.5 if ids else 3.0)


def ensure_historical_background_runner(app) -> bool:
    """Start one daemon scanner per web process when production is ready."""

    if not _runner_enabled(app):
        return False

    redis_url = _redis_url(app)
    if not redis_url or redis_url.startswith("memory://"):
        app.logger.warning(
            "historical_background_runner_not_started reason=redis_unavailable"
        )
        return False

    pid = os.getpid()
    with _RUNNER_START_LOCK:
        if pid in _RUNNER_STARTED_PIDS:
            return True
        thread = threading.Thread(
            target=_runner_loop,
            args=(app,),
            name="aura-historical-background",
            daemon=True,
        )
        thread.start()
        _RUNNER_STARTED_PIDS.add(pid)
    return True
