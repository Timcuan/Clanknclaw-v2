from clankandclaw.utils.extraction import extract_token_identity


def test_extract_token_identity_uses_regex_first():
    result = extract_token_identity("deploy token Pepe symbol PEPE")
    assert result.name == "Pepe"
    assert result.symbol == "PEPE"
    assert result.used_llm is False
