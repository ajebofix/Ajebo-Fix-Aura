"""OpenAI Responses API adapter behind Aura's Rina provider boundary.

The adapter intentionally receives only already-minimized provider context. It
uses bounded SDK timeout/retry settings and disables Responses application-state
storage for Aura requests. Provider exceptions are reduced to privacy-safe
failure classes before they reach orchestration.
"""

from __future__ import annotations

import os
from typing import Any

import openai
from openai import OpenAI

from rina.providers.base import (
    RinaProviderConfigurationError,
    RinaProviderConnectionError,
    RinaProviderQuotaError,
    RinaProviderRateLimitError,
    RinaProviderRejectedError,
    RinaProviderRequest,
    RinaProviderResult,
    RinaProviderTimeoutError,
    RinaProviderTransientError,
)
from services.rina_runtime_flags import (
    rina_openai_max_output_tokens,
    rina_openai_max_retries,
    rina_openai_model,
    rina_openai_reasoning_effort,
    rina_openai_timeout_seconds,
)


class OpenAIRinaProvider:
    provider_name = "openai"

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.model = (model or rina_openai_model()).strip()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else rina_openai_timeout_seconds()
        )
        self.max_retries = (
            max_retries if max_retries is not None else rina_openai_max_retries()
        )
        self.reasoning_effort = rina_openai_reasoning_effort()
        self.max_output_tokens = rina_openai_max_output_tokens()

        if client is not None:
            self._client = client
            return

        # OPENAI_API_KEY is canonical. OPEN_AI_KEY is a temporary compatibility
        # alias documented for Aura's earlier Railway environment. Check both
        # here as well as in package/runtime normalization so provider startup
        # does not depend on import order.
        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPEN_AI_KEY")
            or ""
        ).strip()
        if not api_key:
            raise RinaProviderConfigurationError(
                "OpenAI provider credentials are not configured"
            )

        # The official SDK retries transient connection/408/409/429/5xx errors.
        # Aura caps that behavior here rather than adding a second retry loop.
        self._client = OpenAI(
            api_key=api_key,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )

    def generate(self, request: RinaProviderRequest) -> RinaProviderResult:
        selected_model = request.model_hint or self.model
        # Detailed advisor comparisons may need more room than everyday chats.
        # Keep both paths bounded so one message cannot request unlimited output.
        requested_budget = request.max_output_tokens or self.max_output_tokens
        output_budget = min(5000, max(self.max_output_tokens, int(requested_budget)))
        create_kwargs: dict[str, Any] = {
            "model": selected_model,
            "instructions": request.instructions,
            "input": list(request.input_messages),
            "store": False,
            "max_output_tokens": output_budget,
        }
        if selected_model.startswith(("gpt-5", "gpt-6", "o")):
            create_kwargs["reasoning"] = {"effort": self.reasoning_effort}

        try:
            response = self._client.responses.create(**create_kwargs)
        except openai.APITimeoutError as exc:
            raise RinaProviderTimeoutError(
                "OpenAI provider request timed out"
            ) from exc
        except openai.APIConnectionError as exc:
            raise RinaProviderConnectionError(
                "OpenAI provider connection failed"
            ) from exc
        except openai.RateLimitError as exc:
            code = str(
                getattr(exc, "code", None)
                or (
                    (getattr(exc, "body", None) or {}).get("error", {}).get("code")
                    if isinstance(getattr(exc, "body", None), dict)
                    else ""
                )
                or ""
            ).strip().lower()
            if code in {
                "insufficient_quota",
                "billing_hard_limit_reached",
                "credit_balance_exhausted",
            }:
                raise RinaProviderQuotaError(
                    "OpenAI provider quota or credit balance is unavailable"
                ) from exc
            raise RinaProviderRateLimitError(
                "OpenAI provider rate limit was reached"
            ) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise RinaProviderConfigurationError(
                "OpenAI provider rejected the configured credentials or permissions"
            ) from exc
        except openai.BadRequestError as exc:
            raise RinaProviderRejectedError(
                "OpenAI provider rejected the request contract"
            ) from exc
        except openai.APIStatusError as exc:
            if int(getattr(exc, "status_code", 0) or 0) >= 500:
                raise RinaProviderTransientError(
                    "OpenAI provider returned a transient server failure"
                ) from exc
            raise RinaProviderRejectedError(
                "OpenAI provider rejected the request"
            ) from exc
        except openai.OpenAIError as exc:
            raise RinaProviderTransientError(
                "OpenAI provider request failed"
            ) from exc

        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            raise RinaProviderRejectedError(
                "OpenAI provider returned no usable text output"
            )

        status = str(getattr(response, "status", "") or "").lower()
        incomplete = status == "incomplete"
        if incomplete:
            details = getattr(response, "incomplete_details", None)
            reason = (
                details.get("reason")
                if isinstance(details, dict)
                else getattr(details, "reason", None)
            )
            if reason != "max_output_tokens":
                raise RinaProviderRejectedError(
                    "OpenAI provider did not finish its response"
                )
            # Never misrepresent an unfinished analysis as a complete review.
            text += (
                "\n\n**Review incomplete — response limit reached.** "
                "The remaining analysis was not generated. Ask Rina to continue "
                "from the last unfinished section before relying on the full review. "
                "Nothing was changed in the vehicle record by this read-only answer."
            )
        elif status not in {"", "completed"}:
            raise RinaProviderRejectedError(
                "OpenAI provider returned a non-final response"
            )

        response_model = str(getattr(response, "model", "") or self.model)
        provider_request_id = getattr(response, "_request_id", None)

        return RinaProviderResult(
            text=text,
            provider=self.provider_name,
            model=response_model,
            provider_request_id=(
                str(provider_request_id) if provider_request_id else None
            ),
            incomplete=incomplete,
        )
