from dataclasses import dataclass
import re

from clankandclaw.utils.llm import call_token_identity_fallback
from clankandclaw.utils.parsing import extract_name_hint, extract_symbol_hint


@dataclass
class ExtractionResult:
    name: str
    symbol: str
    used_llm: bool


_PARENS_PAIR_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9 _-]{0,48})\s*\(\s*\$?([A-Za-z0-9]{2,12})\s*\)")


def _extract_name_symbol_pair(text: str) -> tuple[str | None, str | None]:
    matches = list(_PARENS_PAIR_RE.finditer(text))
    if not matches:
        return None, None
    # Use the last match to prefer the nearest token mention in long sentences.
    match = matches[-1]
    name = match.group(1).strip()
    symbol = match.group(2).strip().upper()
    return (name or None, symbol or None)


def extract_token_identity(text: str) -> ExtractionResult:
    pair_name, pair_symbol = _extract_name_symbol_pair(text)
    if pair_name and pair_symbol:
        return ExtractionResult(pair_name, pair_symbol, False)

    symbol = extract_symbol_hint(text)
    name = extract_name_hint(text, symbol)
    
    # If we have both, we are Gold.
    if name and symbol:
        return ExtractionResult(name, symbol, False)
        
    # If we only have symbol, try one more time for a generic name hint.
    if symbol and not name:
        name = extract_name_hint(text, None)
        if name and name != symbol:
             return ExtractionResult(name, symbol, False)
        # Cashtag-only and symbol-only messages should stay deterministic.
        return ExtractionResult(symbol, symbol, False)
    
    try:
        fallback_name, fallback_symbol = call_token_identity_fallback(text)
        resolved_name = fallback_name or name
        resolved_symbol = fallback_symbol or symbol
        if not resolved_name or not resolved_symbol:
            raise ValueError("token identity extraction failed")
        return ExtractionResult(resolved_name, resolved_symbol, True)
    except Exception as exc:
        raise ValueError("token identity extraction failed") from exc
