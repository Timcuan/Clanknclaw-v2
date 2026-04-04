from dataclasses import dataclass
import re


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
    raise ValueError("deterministic extraction failed")
