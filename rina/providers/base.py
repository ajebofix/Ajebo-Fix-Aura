"""Provider-neutral request/result types for Rina language generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RinaProviderRequest:
    request_id: str
    instructions: str
    input_messages: tuple[dict[str, str], ...]
    model_hint: str | None = None


@dataclass(frozen=True)
class RinaProviderResult:
    text: str
    provider: str
    model: str
    provider_request_id: str | None = None


class RinaProviderError(RuntimeError):
    """Base provider failure with a privacy-safe classification."""

    failure_class = "provider_error"
    provider_status = "unavailable"


class RinaProviderConfigurationError(RinaProviderError):
    failure_class = "configuration"
    provider_status = "rejected"


class RinaProviderTransientError(RinaProviderError):
    failure_class = "transient"
    provider_status = "unavailable"


class RinaProviderRejectedError(RinaProviderError):
    failure_class = "rejected"
    provider_status = "rejected"


class RinaLanguageProvider(Protocol):
    provider_name: str

    def generate(self, request: RinaProviderRequest) -> RinaProviderResult:
        ...
