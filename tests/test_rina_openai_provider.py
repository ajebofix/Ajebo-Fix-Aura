from __future__ import annotations

from types import SimpleNamespace

from rina.providers.base import RinaProviderRequest
from rina.providers.openai_provider import OpenAIRinaProvider


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text="Provider response from recorded context.",
            model="gpt-test-snapshot",
            _request_id="req_openai_test_1",
        )


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_openai_adapter_uses_responses_api_without_application_state_storage():
    client = FakeClient()
    provider = OpenAIRinaProvider(
        client=client,
        model="gpt-test-model",
        timeout_seconds=5.0,
        max_retries=1,
    )

    request = RinaProviderRequest(
        request_id="provider-test-request",
        instructions="Use only the supplied recorded context.",
        input_messages=(
            {"role": "user", "content": "Reference data."},
            {"role": "user", "content": "What changed?"},
        ),
    )
    result = provider.generate(request)

    assert result.text == "Provider response from recorded context."
    assert result.provider == "openai"
    assert result.model == "gpt-test-snapshot"
    assert result.provider_request_id == "req_openai_test_1"

    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["model"] == "gpt-test-model"
    assert call["instructions"] == request.instructions
    assert call["input"] == list(request.input_messages)
    assert call["store"] is False
    assert "tools" not in call


def test_request_model_hint_overrides_adapter_default():
    client = FakeClient()
    provider = OpenAIRinaProvider(client=client, model="default-model")
    request = RinaProviderRequest(
        request_id="provider-model-hint",
        instructions="Stay within Aura authority.",
        input_messages=({"role": "user", "content": "Hello."},),
        model_hint="explicit-model",
    )

    provider.generate(request)

    assert client.responses.calls[0]["model"] == "explicit-model"
