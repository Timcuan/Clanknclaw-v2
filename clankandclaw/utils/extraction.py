from dataclasses import dataclass

from clankandclaw.utils.llm import call_token_identity_fallback
from clankandclaw.utils.parsing import extract_name_hint, extract_symbol_hint


@dataclass
class ExtractionResult:
    name: str
    symbol: str
    used_llm: bool


def extract_token_identity(text: str) -> ExtractionResult:
    symbol = extract_symbol_hint(text)
    name = extract_name_hint(text, symbol)
    if name and symbol:
        return ExtractionResult(name, symbol, False)
    if symbol:
        return ExtractionResult(symbol, symbol, False)

    try:
        name, symbol = call_token_identity_fallback(text)
    except Exception as exc:
        raise ValueError(f"token identity extraction failed: {exc}") from exc

    if not name:
        raise ValueError("token identity extraction failed: name is empty")
    if not symbol:
        raise ValueError("token identity extraction failed: symbol is empty")

    return ExtractionResult(name, symbol, True)
