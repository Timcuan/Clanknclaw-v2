import sqlite3
from pathlib import Path
from typing import Optional


class DatabaseManager:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signal_candidates (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    raw_text TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_decisions (
                    candidate_id TEXT PRIMARY KEY,
                    score INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason_codes TEXT NOT NULL,
                    recommended_platform TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                );
                CREATE TABLE IF NOT EXISTS review_items (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                );
                """
            )

    def list_tables(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [row["name"] for row in rows]

    def save_candidate(
        self,
        candidate_id: str,
        source: str,
        source_event_id: str,
        fingerprint: str,
        raw_text: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signal_candidates (id, source, source_event_id, fingerprint, raw_text) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, source, source_event_id, fingerprint, raw_text),
            )

    def save_decision(
        self,
        candidate_id: str,
        score: int,
        decision: str,
        reason_codes: list[str],
        recommended_platform: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO candidate_decisions (candidate_id, score, decision, reason_codes, recommended_platform) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, score, decision, ",".join(reason_codes), recommended_platform),
            )

    def get_candidate_decision(self, candidate_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM candidate_decisions WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
