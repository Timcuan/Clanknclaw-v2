from dataclasses import dataclass
import re

from clankandclaw.utils.llm import call_token_identity_fallback


@dataclass
class ExtractionResult:
    name: str
    symbol: str
    used_llm: bool


def extract_token_identity(text: str) -> ExtractionResult:
    name_match = re.search(r"token\s+([A-Za-z][A-Za-z0-9]{1,20})", text)
    symbol_match = re.search(r"symbol\s+([A-Z0-9]{2,10})", text)
    if name_match and symbol_match:
        return ExtractionResult(name_match.group(1), symbol_match.group(1), False)

    try:
        name, symbol = call_token_identity_fallback(text)
    except Exception as exc:
        raise ValueError(f"token identity extraction failed: {exc}") from exc

    if not name:
        raise ValueError("token identity extraction failed: name is empty")
    if not symbol:
        raise ValueError("token identity extraction failed: symbol is empty")

    return ExtractionResult(name, symbol, True)
