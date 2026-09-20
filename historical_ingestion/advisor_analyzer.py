"""Advisor-grade historical document analysis for Aura.

This module is intentionally separate from ordinary Rina chat generation.
Historical vehicle records deserve a stronger, slower reasoning path because
the output may become long-lived vehicle memory after advisor approval.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import logging
import os
from typing import Any

import openai
from openai import OpenAI

from rina.providers.base import (
    RinaProviderConfigurationError,
    RinaProviderRejectedError,
    RinaProviderTransientError,
)


logger = logging.getLogger(__name__)


def _historical_model() -> str:
    return (
        os.getenv("HISTORICAL_INGESTION_OPENAI_MODEL")
        or "gpt-5.6-sol"
    ).strip()


def _historical_reasoning_effort() -> str:
    value = (
        os.getenv("HISTORICAL_INGESTION_REASONING_EFFORT")
        or "high"
    ).strip().lower()
    return value if value in {"none", "low", "medium", "high", "xhigh"} else "high"


def _historical_timeout_seconds() -> float:
    raw = (os.getenv("HISTORICAL_INGESTION_TIMEOUT_SECONDS") or "75").strip()
    try:
        value = float(raw)
    except ValueError:
        return 75.0
    return min(max(value, 15.0), 120.0)


def _api_key() -> str:
    value = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("OPEN_AI_KEY")
        or ""
    ).strip()
    if not value:
        raise RinaProviderConfigurationError(
            "OpenAI provider credentials are not configured"
        )
    return value


DOCUMENT_UNDERSTANDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "document": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "document_type": {"type": "string"},
                "title": {"type": ["string", "null"]},
                "reference": {"type": ["string", "null"]},
                "job_reference": {"type": ["string", "null"]},
                "sow_reference": {"type": ["string", "null"]},
                "document_date": {"type": ["string", "null"]},
                "client_name": {"type": ["string", "null"]},
                "vehicle_description": {"type": ["string", "null"]},
                "vin": {"type": ["string", "null"]},
                "plate_number": {"type": ["string", "null"]},
            },
            "required": [
                "document_type",
                "title",
                "reference",
                "job_reference",
                "sow_reference",
                "document_date",
                "client_name",
                "vehicle_description",
                "vin",
                "plate_number",
            ],
        },
        "advisor_narrative": {"type": "string"},
        "chronology": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "date": {"type": ["string", "null"]},
                    "event": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "reported",
                            "observed",
                            "recommended",
                            "authorized",
                            "completed",
                            "outcome_observed",
                            "financial_observed",
                            "unknown",
                        ],
                    },
                    "source_pages": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "source_excerpt": {"type": "string"},
                },
                "required": [
                    "date",
                    "event",
                    "status",
                    "source_pages",
                    "source_excerpt",
                ],
            },
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "fact_id": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "identity",
                            "reported_concern",
                            "observation",
                            "diagnosis_context",
                            "work_item",
                            "outcome",
                            "mileage",
                            "financial",
                            "document_reference",
                        ],
                    },
                    "state": {
                        "type": "string",
                        "enum": [
                            "reported",
                            "observed",
                            "recommended",
                            "authorized",
                            "completed",
                            "outcome_observed",
                            "unknown",
                        ],
                    },
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "source_pages": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "source_excerpt": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                },
                "required": [
                    "fact_id",
                    "kind",
                    "state",
                    "title",
                    "detail",
                    "date",
                    "source_pages",
                    "source_excerpt",
                    "why_it_matters",
                ],
            },
        },
        "ambiguities": {
            "type": "array",
            "items": {"type": "string"},
        },
        "advisor_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "document",
        "advisor_narrative",
        "chronology",
        "facts",
        "ambiguities",
        "advisor_suggestions",
    ],
}


CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "document": DOCUMENT_UNDERSTANDING_SCHEMA["properties"]["document"],
        "rina_summary": {"type": "string"},
        "advisor_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "document_reference",
                            "reported_concern",
                            "observation",
                            "diagnosis_context",
                            "work_item",
                            "outcome",
                            "mileage",
                            "financial",
                        ],
                    },
                    "state": {
                        "type": "string",
                        "enum": [
                            "reported",
                            "observed",
                            "recommended",
                            "authorized",
                            "completed",
                            "outcome_observed",
                            "unknown",
                        ],
                    },
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "occurred_at": {"type": ["string", "null"]},
                    "source_pages": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "source_fact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source_excerpt": {"type": "string"},
                    "confidence": {"type": "number"},
                    "confidence_reason": {"type": "string"},
                    "suggested_destination": {
                        "type": "string",
                        "enum": [
                            "context_only",
                            "reported_concern",
                            "assessment",
                            "treatment_plan",
                            "treatment_action",
                            "treatment_outcome",
                            "mileage_observation",
                            "vehicle_identity",
                            "financial_separate",
                        ],
                    },
                    "outcome_direction": {
                        "type": "string",
                        "enum": [
                            "improving",
                            "stable",
                            "deteriorating",
                            "resolved",
                            "insufficient_evidence",
                        ],
                    },
                    "advisor_attention": {"type": "string"},
                    "action": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "service",
                                    "component_replacement",
                                    "other_intervention",
                                ],
                            },
                            "component_name": {"type": ["string", "null"]},
                            "component_location": {"type": ["string", "null"]},
                            "component_condition": {
                                "type": "string",
                                "enum": [
                                    "new",
                                    "preowned_tokunbo",
                                    "refurbished",
                                    "client_supplied",
                                    "unknown",
                                    "not_applicable",
                                ],
                            },
                            "quantity": {"type": ["integer", "null"]},
                            "odometer_km": {"type": ["integer", "null"]},
                        },
                        "required": [
                            "kind",
                            "component_name",
                            "component_location",
                            "component_condition",
                            "quantity",
                            "odometer_km",
                        ],
                    },
                },
                "required": [
                    "category",
                    "state",
                    "title",
                    "detail",
                    "occurred_at",
                    "source_pages",
                    "source_fact_ids",
                    "source_excerpt",
                    "confidence",
                    "confidence_reason",
                    "suggested_destination",
                    "outcome_direction",
                    "advisor_attention",
                    "action",
                ],
            },
        },
    },
    "required": [
        "document",
        "rina_summary",
        "advisor_suggestions",
        "candidates",
    ],
}


@dataclass(frozen=True)
class HistoricalAdvisorAnalysis:
    understanding: dict[str, Any]
    structured: dict[str, Any]
    provider: str
    model: str
    understanding_request_id: str | None
    structured_request_id: str | None
    direct_pdf_used: bool = True


class HistoricalAdvisorAnalyzer:
    """Two-pass PDF analysis using a dedicated high-reasoning model."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.model = (model or _historical_model()).strip()
        self.reasoning_effort = (
            reasoning_effort or _historical_reasoning_effort()
        ).strip()
        self._client = client or OpenAI(
            api_key=_api_key(),
            timeout=_historical_timeout_seconds(),
            max_retries=1,
        )

    @staticmethod
    def _response_json(response: Any) -> dict[str, Any]:
        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            raise RinaProviderRejectedError(
                "Historical document provider returned no usable output"
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RinaProviderRejectedError(
                "Historical document provider returned invalid structured output"
            ) from exc
        if not isinstance(payload, dict):
            raise RinaProviderRejectedError(
                "Historical document provider returned an invalid object"
            )
        return payload

    @staticmethod
    def _safe_provider_detail(exc: Exception) -> str:
        status = getattr(exc, "status_code", None)
        request_id = getattr(exc, "request_id", None) or getattr(exc, "_request_id", None)
        body = getattr(exc, "body", None)
        error_type = None
        error_code = None
        error_param = None
        error_message = None

        if isinstance(body, dict):
            error = body.get("error") if isinstance(body.get("error"), dict) else body
            if isinstance(error, dict):
                error_type = error.get("type")
                error_code = error.get("code")
                error_param = error.get("param")
                error_message = error.get("message")

        parts = [
            f"status={status}" if status else None,
            f"type={error_type}" if error_type else None,
            f"code={error_code}" if error_code else None,
            f"param={error_param}" if error_param else None,
            f"request_id={request_id}" if request_id else None,
        ]
        detail = " ".join(part for part in parts if part)
        if error_message:
            safe_message = str(error_message).replace("\n", " ").strip()[:500]
            detail = f"{detail} message={safe_message}".strip()
        return detail or type(exc).__name__

    def _call(
        self,
        *,
        instructions: str,
        input_content: list[dict[str, Any]],
        schema_name: str,
        schema: dict[str, Any],
        stage: str,
    ) -> tuple[dict[str, Any], str | None, str]:
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=instructions,
                input=[
                    {
                        "role": "user",
                        "content": input_content,
                    }
                ],
                reasoning={"effort": self.reasoning_effort},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                store=False,
            )
        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
        ) as exc:
            raise RinaProviderTransientError(
                "Historical document analysis is temporarily unavailable"
            ) from exc
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
        ) as exc:
            raise RinaProviderConfigurationError(
                "Historical document analysis credentials were rejected"
            ) from exc
        except openai.BadRequestError as exc:
            detail = self._safe_provider_detail(exc)
            logger.warning(
                "historical_openai_rejected stage=%s model=%s %s",
                stage,
                self.model,
                detail,
            )
            raise RinaProviderRejectedError(
                f"Historical document analysis request was rejected ({detail})"
            ) from exc
        except openai.APIStatusError as exc:
            detail = self._safe_provider_detail(exc)
            logger.warning(
                "historical_openai_status_error stage=%s model=%s %s",
                stage,
                self.model,
                detail,
            )
            if int(getattr(exc, "status_code", 0) or 0) >= 500:
                raise RinaProviderTransientError(
                    f"Historical document analysis returned a transient failure ({detail})"
                ) from exc
            raise RinaProviderRejectedError(
                f"Historical document analysis request was rejected ({detail})"
            ) from exc
        except openai.OpenAIError as exc:
            raise RinaProviderTransientError(
                "Historical document analysis failed"
            ) from exc

        payload = self._response_json(response)
        request_id = getattr(response, "_request_id", None)
        response_model = str(getattr(response, "model", "") or self.model)
        return (
            payload,
            str(request_id) if request_id else None,
            response_model,
        )

    def analyze_pdf(
        self,
        *,
        pdf_payload: bytes,
        extracted_text: str,
        trusted_vehicle_context: dict[str, Any],
    ) -> HistoricalAdvisorAnalysis:
        encoded_pdf = base64.b64encode(pdf_payload).decode("ascii")

        understanding_instructions = """
You are A.J. Rina performing historical vehicle-record analysis for an AJEBO FIX
PROFESSIONAL ADVISOR. You are not speaking to the vehicle owner.

Read the ENTIRE uploaded PDF before forming conclusions. Treat it as a real
automotive care file: headings, tables, dated updates, appendices, notes, payment
sections and later amendments can change the meaning of earlier text.

Your job in this first pass is DOCUMENT UNDERSTANDING, not database entry.

Reconstruct:
- what document this is and its references;
- which vehicle/client/job it concerns;
- why the vehicle came under care;
- what was reported by the owner;
- what Ajebo Fix actually observed/tested/measured;
- what remained uncertain;
- what was recommended;
- what was authorised;
- what the document explicitly proves was completed;
- what outcomes were actually observed after work;
- the chronology of updates;
- financial/commercial facts separately.

CRITICAL AUTHORITY RULES:
- "recommended", "quoted", "authorised", "purchased", "paid" and "completed"
  are not synonyms;
- future wording ("will replace", "will reassess", "to be checked") is not an
  outcome and is not completed work;
- a payment or final job value does not prove a component was installed;
- never invent an event date from a nearby document date;
- never invent a diagnosis, result, odometer, VIN, component condition or outcome;
- distinguish owner-reported symptoms from advisor observations;
- preserve uncertainty explicitly;
- every extracted fact must cite the page(s) and a short source excerpt;
- advisor suggestions may identify what should be checked/confirmed, but must be
  clearly separate from what the source proves.

Think like a senior Ajebo Fix advisor preparing another advisor to understand the
case quickly and accurately.
""".strip()

        context_text = (
            "Trusted Aura vehicle context (use for disambiguation only; "
            "do not overwrite source evidence):\n"
            + json.dumps(
                trusted_vehicle_context,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n\nPage-preserved searchable extraction:\n"
            + extracted_text
        )

        direct_pdf_used = True
        try:
            understanding, understanding_request_id, response_model = self._call(
                instructions=understanding_instructions,
                input_content=[
                    {
                        "type": "input_text",
                        "text": (
                            context_text
                            + "\n\nThe original PDF is attached and is the primary source."
                        ),
                    },
                    {
                        "type": "input_file",
                        "filename": "historical_vehicle_record.pdf",
                        "file_data": encoded_pdf,
                    },
                ],
                schema_name="aura_historical_document_understanding",
                schema=DOCUMENT_UNDERSTANDING_SCHEMA,
                stage="whole_pdf_understanding",
            )
        except RinaProviderRejectedError as direct_pdf_error:
            direct_pdf_used = False
            logger.warning(
                "historical_pdf_direct_input_fallback model=%s reason=%s",
                self.model,
                str(direct_pdf_error)[:800],
            )
            understanding, understanding_request_id, response_model = self._call(
                instructions=(
                    understanding_instructions
                    + "\n\nThe original PDF could not be accepted by the provider. "
                    "Use the complete page-preserved text below as the authoritative "
                    "representation for this pass. Do not weaken the evidence rules."
                ),
                input_content=[
                    {
                        "type": "input_text",
                        "text": context_text,
                    }
                ],
                schema_name="aura_historical_document_understanding",
                schema=DOCUMENT_UNDERSTANDING_SCHEMA,
                stage="page_preserved_text_fallback",
            )

        structuring_instructions = """
You are A.J. Rina converting an already-read historical vehicle document into
candidate Aura records for an AJEBO FIX PROFESSIONAL ADVISOR.

Use ONLY the document-understanding object supplied by the previous pass. Do not
invent new source facts. This is not a second opportunity to reinterpret the PDF.

Build review candidates that help an advisor create accurate vehicle memory.

Rules:
- owner complaint -> Reported Concern only when the source says it was reported;
- advisor/test finding -> Assessment;
- recommended or authorised work -> Treatment Plan;
- Treatment Action with state=completed ONLY when a cited source fact explicitly
  proves the work was performed, installed, replaced or completed;
- a planned reassessment is Treatment Plan/context, never Treatment Outcome;
- Treatment Outcome requires an actual post-work observation/result;
- financial facts ALWAYS go to Financial Separate and use state=observed;
- do not convert job value, invoice status or payment into mechanical completion;
- occurred_at must be null unless that specific fact has an evidenced date;
- retain source page numbers and exact/near-exact source excerpt;
- keep separate facts separate instead of collapsing an entire job into one card;
- use preowned_tokunbo only when the source actually establishes pre-owned/Tokunbo;
- suggestions must be concise plain strings for an Ajebo Fix advisor.

The review screen is a professional verification surface, not a diagnosis engine.
""".strip()

        structured, structured_request_id, structured_model = self._call(
            instructions=structuring_instructions,
            input_content=[
                {
                    "type": "input_text",
                    "text": (
                        "Trusted vehicle context:\n"
                        + json.dumps(
                            trusted_vehicle_context,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nDocument understanding from pass 1:\n"
                        + json.dumps(
                            understanding,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                }
            ],
            schema_name="aura_historical_review_candidates",
            schema=CANDIDATE_SCHEMA,
            stage="advisor_record_structuring",
        )

        return HistoricalAdvisorAnalysis(
            understanding=understanding,
            structured=structured,
            provider=self.provider_name,
            model=structured_model or response_model,
            understanding_request_id=understanding_request_id,
            structured_request_id=structured_request_id,
            direct_pdf_used=direct_pdf_used,
        )
