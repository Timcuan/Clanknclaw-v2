import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DatabaseManager:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _existing_tables(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row["name"] for row in rows}

    def _review_items_has_fresh_schema(self, conn: sqlite3.Connection) -> bool:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(review_items)").fetchall()]
        expected_columns = [
            "id",
            "candidate_id",
            "status",
            "created_at",
            "expires_at",
            "locked_by",
            "locked_at",
        ]
        if columns != expected_columns:
            return False
        foreign_keys = conn.execute("PRAGMA foreign_key_list(review_items)").fetchall()
        return any(
            row["from"] == "candidate_id"
            and row["table"] == "signal_candidates"
            and row["to"] == "id"
            for row in foreign_keys
        )

    def _rebuild_review_items_table(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(review_items)").fetchall()
        }
        orphan_candidate_ids = [
            row["candidate_id"]
            for row in conn.execute(
                """
                SELECT DISTINCT legacy.candidate_id
                FROM review_items AS legacy
                LEFT JOIN signal_candidates AS candidates
                    ON candidates.id = legacy.candidate_id
                WHERE candidates.id IS NULL
                """
            ).fetchall()
        ]
        if orphan_candidate_ids:
            raise sqlite3.IntegrityError(
                "Cannot rebuild review_items: missing signal_candidates for candidate_id(s): "
                + ", ".join(sorted(orphan_candidate_ids))
            )

        created_at_fallback = _utc_now_iso()
        select_parts = [
            "id",
            "candidate_id",
            "status",
            "COALESCE(created_at, ?) AS created_at" if "created_at" in columns else "? AS created_at",
            "expires_at",
            "locked_by" if "locked_by" in columns else "NULL AS locked_by",
            "locked_at" if "locked_at" in columns else "NULL AS locked_at",
        ]

        conn.execute("SAVEPOINT review_items_rebuild")
        try:
            conn.execute("ALTER TABLE review_items RENAME TO review_items_legacy")
            conn.execute(
                """
                CREATE TABLE review_items (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    locked_by TEXT,
                    locked_at TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                );
                """
            )
            conn.execute(
                f"""
                INSERT INTO review_items (id, candidate_id, status, created_at, expires_at, locked_by, locked_at)
                SELECT {", ".join(select_parts)}
                FROM review_items_legacy
                """,
                (created_at_fallback,),
            )
            conn.execute("DROP TABLE review_items_legacy")
            conn.execute("RELEASE SAVEPOINT review_items_rebuild")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT review_items_rebuild")
            conn.execute("RELEASE SAVEPOINT review_items_rebuild")
            raise

    def _legacy_review_items_orphan_candidate_ids(self, conn: sqlite3.Connection) -> list[str]:
        existing_tables = self._existing_tables(conn)
        if "review_items" not in existing_tables:
            return []

        if "signal_candidates" in existing_tables:
            query = """
                SELECT DISTINCT legacy.candidate_id
                FROM review_items AS legacy
                LEFT JOIN signal_candidates AS candidates
                    ON candidates.id = legacy.candidate_id
                WHERE candidates.id IS NULL
            """
        else:
            query = """
                SELECT DISTINCT candidate_id
                FROM review_items
            """

        return [row["candidate_id"] for row in conn.execute(query).fetchall()]

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                existing_tables = self._existing_tables(conn)
                review_items_exists = "review_items" in existing_tables
                legacy_review_items = review_items_exists and not self._review_items_has_fresh_schema(conn)

                if legacy_review_items:
                    orphan_candidate_ids = self._legacy_review_items_orphan_candidate_ids(conn)
                    if orphan_candidate_ids:
                        raise sqlite3.IntegrityError(
                            "Cannot rebuild review_items: missing signal_candidates for candidate_id(s): "
                            + ", ".join(sorted(orphan_candidate_ids))
                        )

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS signal_candidates (
                        id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        source_event_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        raw_text TEXT NOT NULL,
                        observed_at TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );
                    """
                )
                # Migrate existing signal_candidates tables that are missing new columns
                sc_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(signal_candidates)").fetchall()
                }
                if "observed_at" not in sc_columns:
                    conn.execute(
                        "ALTER TABLE signal_candidates ADD COLUMN observed_at TEXT NOT NULL DEFAULT ''"
                    )
                if "metadata_json" not in sc_columns:
                    conn.execute(
                        "ALTER TABLE signal_candidates ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
                    )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS candidate_decisions (
                        candidate_id TEXT PRIMARY KEY,
                        score INTEGER NOT NULL,
                        decision TEXT NOT NULL,
                        reason_codes TEXT NOT NULL,
                        recommended_platform TEXT NOT NULL,
                        FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS review_items (
                        id TEXT PRIMARY KEY,
                        candidate_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        locked_by TEXT,
                        locked_at TEXT,
                        FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                    );
                    """
                )
                if legacy_review_items:
                    self._rebuild_review_items_table(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

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
        observed_at: str = "",
        metadata: dict | None = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO signal_candidates
                    (id, source, source_event_id, fingerprint, raw_text, observed_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (candidate_id, source, source_event_id, fingerprint, raw_text, observed_at, metadata_json),
            )

    def save_candidate_and_decision(
        self,
        candidate_id: str,
        source: str,
        source_event_id: str,
        fingerprint: str,
        raw_text: str,
        score: int,
        decision: str,
        reason_codes: list[str],
        recommended_platform: str,
        observed_at: str = "",
        metadata: dict | None = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {})
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """
                    INSERT INTO signal_candidates
                        (id, source, source_event_id, fingerprint, raw_text, observed_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source = excluded.source,
                        source_event_id = excluded.source_event_id,
                        fingerprint = excluded.fingerprint,
                        raw_text = excluded.raw_text,
                        observed_at = excluded.observed_at,
                        metadata_json = excluded.metadata_json
                    """,
                    (candidate_id, source, source_event_id, fingerprint, raw_text, observed_at, metadata_json),
                )
                conn.execute(
                    """
                    INSERT INTO candidate_decisions (candidate_id, score, decision, reason_codes, recommended_platform)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        score = excluded.score,
                        decision = excluded.decision,
                        reason_codes = excluded.reason_codes,
                        recommended_platform = excluded.recommended_platform
                    """,
                    (candidate_id, score, decision, ",".join(reason_codes), recommended_platform),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

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

    def get_candidate(self, candidate_id: str) -> Optional[sqlite3.Row]:
        """Get candidate by ID."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM signal_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()

    def create_review_item(self, review_id: str, candidate_id: str, expires_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO review_items (id, candidate_id, status, created_at, expires_at, locked_by, locked_at) VALUES (?, ?, 'pending', ?, ?, NULL, NULL)",
                (review_id, candidate_id, _utc_now_iso(), expires_at),
            )

    def get_review_item(self, review_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM review_items WHERE id = ?",
                (review_id,),
            ).fetchone()

    def lock_review_item(self, review_id: str, locked_by: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM review_items WHERE id = ?",
                (review_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                return False
            cur = conn.execute(
                "UPDATE review_items SET status = 'deploying', locked_by = ?, locked_at = ? WHERE id = ? AND status = 'pending'",
                (locked_by, _utc_now_iso(), review_id),
            )
        return cur.rowcount == 1
