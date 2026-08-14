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
    RinaProviderRejectedError,
    RinaProviderRequest,
    RinaProviderResult,
    RinaProviderTransientError,
)
from services.rina_runtime_flags import (
    rina_openai_max_retries,
    rina_openai_model,
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
        try:
            response = self._client.responses.create(
                model=request.model_hint or self.model,
                instructions=request.instructions,
                input=list(request.input_messages),
                store=False,
            )
        except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError) as exc:
            raise RinaProviderTransientError(
                "OpenAI provider is temporarily unavailable"
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

        response_model = str(getattr(response, "model", "") or self.model)
        provider_request_id = getattr(response, "_request_id", None)

        return RinaProviderResult(
            text=text,
            provider=self.provider_name,
            model=response_model,
            provider_request_id=(
                str(provider_request_id) if provider_request_id else None
            ),
        )
