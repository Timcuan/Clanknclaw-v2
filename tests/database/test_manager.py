from clankandclaw.database.manager import DatabaseManager


def test_database_manager_initializes_schema(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    tables = db.list_tables()
    assert "signal_candidates" in tables
    assert "review_items" in tables


def test_database_manager_persists_candidate_and_decision(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.save_candidate("sig-1", "x", "tweet-1", "fp-1", "deploy pepe")
    db.save_decision("sig-1", 85, "priority_review", ["keyword_match"], "clanker")
    row = db.get_candidate_decision("sig-1")
    assert row["decision"] == "priority_review"
