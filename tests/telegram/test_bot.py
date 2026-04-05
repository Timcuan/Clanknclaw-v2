from clankandclaw.telegram.bot import build_review_message


def test_build_review_message_contains_priority_and_candidate_id():
    text = build_review_message("sig-1", "priority_review", 85, ["deploy_keyword", "base_context"])
    assert "sig-1" in text
    assert "priority_review" in text
