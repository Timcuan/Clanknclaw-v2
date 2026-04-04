class ReviewQueue:
    def __init__(self, db):
        self.db = db

    def create(self, review_id: str, candidate_id: str, expires_at: str) -> None:
        self.db.create_review_item(review_id, candidate_id, expires_at)

    def lock(self, review_id: str, locked_by: str) -> bool:
        return self.db.lock_review_item(review_id, locked_by)
