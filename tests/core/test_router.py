from clankandclaw.core.router import route_candidate


def test_router_marks_high_scores_as_priority_review():
    route = route_candidate(score=85)
    assert route.review_priority == "priority_review"
