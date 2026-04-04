from dataclasses import dataclass


@dataclass
class RouteResult:
    recommended_platform: str
    review_priority: str
    decision: str


def route_candidate(score: int) -> RouteResult:
    if score >= 80:
        return RouteResult("clanker", "priority_review", "priority_review")
    if score >= 60:
        return RouteResult("clanker", "review", "review")
    return RouteResult("clanker", "review", "skip")
