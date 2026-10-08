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
    assert call["max_output_tokens"] == provider.max_output_tokens
    assert "reasoning" not in call
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


def test_advisor_plan_source_comparison_gets_a_larger_bounded_budget(monkeypatch):
    from services.rina_provider_context import _advisor_source_comparison_budget

    message = (
        "Review the current Collision Repair Treatment Plan against the latest "
        "WhatsApp conversation and all newly processed media evidence. Do not edit."
    )
    assert _advisor_source_comparison_budget(
        authority="administrator", message=message
    ) == 4800
    assert _advisor_source_comparison_budget(
        authority="owner", message=message
    ) is None
    assert _advisor_source_comparison_budget(
        authority="administrator", message="Where is the vehicle?"
    ) is None

    monkeypatch.setenv("RINA_OPENAI_MAX_OUTPUT_TOKENS", "1800")
    client = FakeClient()
    provider = OpenAIRinaProvider(client=client, model="gpt-5.6-terra")
    request = RinaProviderRequest(
        request_id="complex-comparison",
        instructions="Stay within scoped vehicle evidence.",
        input_messages=({"role": "user", "content": message},),
        max_output_tokens=4800,
    )
    provider.generate(request)
    assert client.responses.calls[0]["max_output_tokens"] == 4800


def test_incomplete_provider_output_is_explicitly_flagged_and_preserved():
    client = FakeClient()
    client.responses.create = lambda **kwargs: SimpleNamespace(
        output_text="1. **Washer reservoir",
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        model="gpt-5.6-terra",
        _request_id="req-incomplete",
    )
    provider = OpenAIRinaProvider(client=client, model="gpt-5.6-terra")
    request = RinaProviderRequest(
        request_id="incomplete",
        instructions="Be evidence-led",
        input_messages=({"role": "user", "content": "Review the plan"},),
    )
    result = provider.generate(request)
    assert result.incomplete is True
    assert result.text.startswith("1. **Washer reservoir")
    assert "Review incomplete" in result.text
    assert "Ask Rina to continue" in result.text


def test_completed_provider_output_does_not_claim_incomplete():
    client = FakeClient()
    provider = OpenAIRinaProvider(client=client, model="gpt-5.6-terra")
    request = RinaProviderRequest(
        request_id="complete",
        instructions="Be evidence-led",
        input_messages=({"role": "user", "content": "Hello"},),
    )
    assert provider.generate(request).incomplete is False


def test_frontier_model_uses_bounded_reasoning_and_output_budget(monkeypatch):
    monkeypatch.setenv("RINA_OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("RINA_OPENAI_MAX_OUTPUT_TOKENS", "1800")
    client = FakeClient()
    provider = OpenAIRinaProvider(client=client, model="gpt-5.6-terra")
    request = RinaProviderRequest(
        request_id="provider-frontier-policy",
        instructions="Use only recorded Aura context.",
        input_messages=({"role": "user", "content": "Summarise this vehicle."},),
    )

    provider.generate(request)

    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-terra"
    assert call["reasoning"] == {"effort": "low"}
    assert call["max_output_tokens"] == 1800
    assert call["store"] is False
