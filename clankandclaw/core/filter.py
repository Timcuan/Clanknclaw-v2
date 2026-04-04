from dataclasses import dataclass

from clankandclaw.models.token import SignalCandidate


@dataclass
class FilterDecision:
    allowed: bool
    reason_codes: list[str]


def quick_filter(candidate: SignalCandidate) -> FilterDecision:
    lowered = candidate.raw_text.lower()
    if "deploy" not in lowered and "launch" not in lowered:
        return FilterDecision(False, ["missing_deploy_keyword"])
    return FilterDecision(True, ["keyword_match"])
