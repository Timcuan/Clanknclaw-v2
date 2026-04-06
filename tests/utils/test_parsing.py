from clankandclaw.utils.parsing import (
    extract_chain_hints,
    extract_contracts,
    extract_name_hint,
    extract_symbol_hint,
)


def test_extract_symbol_hint_supports_lowercase_symbol_keyword():
    assert extract_symbol_hint("deploy token moon symbol pepe") == "PEPE"


def test_extract_symbol_hint_supports_ticker_with_punctuation():
    assert extract_symbol_hint("ticker: $mo-on_2 now") == "MOON2"


def test_extract_name_hint_extracts_structured_name():
    assert extract_name_hint("token name: Moon Runner symbol MR") == "Moon Runner"


def test_extract_chain_hints_canonicalizes_aliases():
    assert extract_chain_hints("deploy on sol and eth then base") == ["base", "ethereum", "solana"]


def test_extract_contracts_filters_non_numeric_solana_like_words():
    text = "launch ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwx"
    evm, sol = extract_contracts(text)
    assert evm == []
    assert sol == []
