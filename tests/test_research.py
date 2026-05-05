"""Tests for jobbuddy.research — research_company error/happy paths.

No live LLM calls. Uses a stub OpenAI client that returns shaped responses."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobbuddy.research import (
    PermanentResearchError,
    research_company,
)
from jobbuddy.settings import Settings


def _stub_settings() -> Settings:
    return Settings(
        research_model="test-model",
        research_endpoint="https://test.cognitiveservices.azure.com",
    )


def _stub_response(*, output_text: str, output_items: list | None = None,
                   incomplete_reason: str | None = None) -> SimpleNamespace:
    incomplete = (
        SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
    )
    return SimpleNamespace(
        output_text=output_text,
        output=output_items or [],
        incomplete_details=incomplete,
    )


def _stub_client(response) -> MagicMock:
    client = MagicMock()
    client.responses.create.return_value = response
    return client


def test_happy_path_returns_parsed_bio():
    output_items = [
        SimpleNamespace(type="web_search_call"),
        SimpleNamespace(type="web_search_call"),
        SimpleNamespace(type="message"),
    ]
    response = _stub_response(
        output_text=json.dumps({"short_bio": "short prose", "long_bio": "long prose"}),
        output_items=output_items,
    )
    bio = research_company("Acme", client=_stub_client(response), settings=_stub_settings())

    assert bio.short_bio == "short prose"
    assert bio.long_bio == "long prose"
    assert bio.model == "test-model"
    assert bio.web_search_count == 2


def test_content_filter_raises_permanent():
    response = _stub_response(output_text="", incomplete_reason="content_filter")
    with pytest.raises(PermanentResearchError, match="content_filter"):
        research_company("Acme", client=_stub_client(response), settings=_stub_settings())


def test_other_incomplete_reason_raises_permanent():
    response = _stub_response(output_text="", incomplete_reason="max_output_tokens")
    with pytest.raises(PermanentResearchError, match="max_output_tokens"):
        research_company("Acme", client=_stub_client(response), settings=_stub_settings())


def test_empty_output_raises_permanent():
    response = _stub_response(output_text="")
    with pytest.raises(PermanentResearchError, match="empty response"):
        research_company("Acme", client=_stub_client(response), settings=_stub_settings())


def test_malformed_json_raises_permanent():
    response = _stub_response(output_text="not json {")
    with pytest.raises(PermanentResearchError, match="non-JSON"):
        research_company("Acme", client=_stub_client(response), settings=_stub_settings())


def test_missing_short_bio_raises_permanent():
    response = _stub_response(
        output_text=json.dumps({"long_bio": "ok", "short_bio": ""}),
    )
    with pytest.raises(PermanentResearchError, match="missing bio fields"):
        research_company("Acme", client=_stub_client(response), settings=_stub_settings())


def test_missing_long_bio_raises_permanent():
    response = _stub_response(
        output_text=json.dumps({"short_bio": "ok", "long_bio": ""}),
    )
    with pytest.raises(PermanentResearchError, match="missing bio fields"):
        research_company("Acme", client=_stub_client(response), settings=_stub_settings())


def test_response_output_none_does_not_crash():
    """Defensive: response.output may be None on unexpected shapes."""
    response = SimpleNamespace(
        output_text=json.dumps({"short_bio": "s", "long_bio": "l"}),
        output=None,
        incomplete_details=None,
    )
    bio = research_company("Acme", client=_stub_client(response), settings=_stub_settings())
    assert bio.web_search_count == 0


def test_strips_whitespace_around_bios():
    response = _stub_response(
        output_text=json.dumps({"short_bio": "  short  ", "long_bio": "\nlong\n"}),
    )
    bio = research_company("Acme", client=_stub_client(response), settings=_stub_settings())
    assert bio.short_bio == "short"
    assert bio.long_bio == "long"


def test_passes_model_and_company_name_through():
    response = _stub_response(
        output_text=json.dumps({"short_bio": "s", "long_bio": "l"}),
    )
    client = _stub_client(response)
    research_company("Anthropic", client=client, settings=_stub_settings())

    call_kwargs = client.responses.create.call_args.kwargs
    assert call_kwargs["model"] == "test-model"
    user_msg = call_kwargs["input"][1]["content"]
    assert "<name>Anthropic</name>" in user_msg


def test_has_research_recognizes_documented_endpoints():
    """Regression: the documented prod endpoint cognitive.microsoft.com must pass."""
    assert Settings(research_endpoint="https://x.openai.azure.com").has_research
    assert Settings(research_endpoint="https://x.cognitiveservices.azure.com").has_research
    assert Settings(research_endpoint="https://westus3.api.cognitive.microsoft.com").has_research


def test_has_research_rejects_lookalike():
    assert not Settings(research_endpoint="https://attacker.com").has_research
    assert not Settings(research_endpoint="").has_research
    assert not Settings(research_endpoint=None).has_research
