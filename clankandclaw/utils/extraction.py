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
    
    # If we have both, we are Gold.
    if name and symbol:
        return ExtractionResult(name, symbol, False)
        
    # If we only have symbol, try one more time for a generic name hint
    if symbol and not name:
        name = extract_name_hint(text, None)
        if name and name != symbol:
             return ExtractionResult(name, symbol, False)
        # If still no name, proceed to fallback rather than just setting name=symbol
    
    try:
        fallback_name, fallback_symbol = call_token_identity_fallback(text)
        return ExtractionResult(
            fallback_name or name or "Community Token",
            fallback_symbol or symbol or "TKN",
            True
        )
    except Exception as exc:
        # Final safety net: Never crash the pipeline
        return ExtractionResult(
            name or "Community Token",
            symbol or "TKN",
            False
        )
