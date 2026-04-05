import pytest

import clankandclaw.utils.extraction as extraction


def test_extract_token_identity_uses_regex_first():
    result = extraction.extract_token_identity("deploy token Pepe symbol PEPE")
    assert result.name == "Pepe"
    assert result.symbol == "PEPE"
    assert result.used_llm is False


def test_extract_token_identity_uses_llm_fallback_when_regex_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_llm_fallback(text: str) -> tuple[str, str]:
        assert text == "launch something ambiguous"
        return ("Pepe", "PEPE")

    monkeypatch.setattr(extraction, "call_token_identity_fallback", fake_llm_fallback)

    result = extraction.extract_token_identity("launch something ambiguous")

    assert result.name == "Pepe"
    assert result.symbol == "PEPE"
    assert result.used_llm is True


def test_extract_token_identity_raises_when_regex_and_fallback_fail(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_llm_fallback(text: str) -> tuple[str, str]:
        raise NotImplementedError("fallback unavailable")

    monkeypatch.setattr(extraction, "call_token_identity_fallback", fake_llm_fallback)

    with pytest.raises(ValueError, match="token identity extraction failed"):
        extraction.extract_token_identity("launch something ambiguous")
