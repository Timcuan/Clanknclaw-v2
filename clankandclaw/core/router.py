from dataclasses import dataclass


@dataclass
class RouteResult:
    recommended_platform: str
    review_priority: str
    decision: str
    auto_trigger: bool = False


def route_candidate(score: int) -> RouteResult:
    """
    Decide the route and automated action based on score.
    - Score >= 90: AUTO-DEPLOY (if mode is auto)
    - Score >= 80: PRIORITY REVIEW
    - Score >= 60: REVIEW
    - Others: SKIP
    """
    if score >= 90:
        return RouteResult("clanker", "priority_review", "auto_deploy", auto_trigger=True)
    if score >= 80:
        return RouteResult("clanker", "priority_review", "priority_review", auto_trigger=False)
    if score >= 60:
        return RouteResult("clanker", "review", "review")
    return RouteResult("clanker", "review", "skip")
