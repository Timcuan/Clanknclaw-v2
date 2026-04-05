def build_review_message(candidate_id: str, review_priority: str, score: int, reason_codes: list[str]) -> str:
    reasons = ", ".join(reason_codes)
    return (
        f"candidate={candidate_id}\n"
        f"priority={review_priority}\n"
        f"score={score}\n"
        f"reasons={reasons}"
    )
