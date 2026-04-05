import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DatabaseManager:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA temp_store = MEMORY")
        return conn

    def _with_retry(self, fn):
        attempts = 3
        for attempt in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                    raise
                sleep(0.05 * (attempt + 1))

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
            "telegram_message_id",
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
            "telegram_message_id" if "telegram_message_id" in columns else "NULL AS telegram_message_id",
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
                    telegram_message_id INTEGER,
                    FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                );
                """
            )
            conn.execute(
                f"""
                INSERT INTO review_items (id, candidate_id, status, created_at, expires_at, locked_by, locked_at, telegram_message_id)
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
                        telegram_message_id INTEGER,
                        FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                    );
                    """
                )
                # Migrate review_items missing telegram_message_id
                ri_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(review_items)").fetchall()
                }
                if "telegram_message_id" not in ri_columns and "review_items" in existing_tables and not legacy_review_items:
                    conn.execute(
                        "ALTER TABLE review_items ADD COLUMN telegram_message_id INTEGER"
                    )
                if legacy_review_items:
                    self._rebuild_review_items_table(conn)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS deployment_results (
                        id TEXT PRIMARY KEY,
                        candidate_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        tx_hash TEXT,
                        contract_address TEXT,
                        error_code TEXT,
                        error_message TEXT,
                        latency_ms INTEGER NOT NULL DEFAULT 0,
                        deployed_at TEXT NOT NULL,
                        FOREIGN KEY (candidate_id) REFERENCES signal_candidates(id)
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reward_claim_results (
                        id TEXT PRIMARY KEY,
                        token_address TEXT NOT NULL,
                        status TEXT NOT NULL,
                        tx_hash TEXT,
                        error_code TEXT,
                        error_message TEXT,
                        claimed_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_review_items_status_expires ON review_items(status, expires_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_review_items_created_at ON review_items(created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_candidates_observed_at ON signal_candidates(observed_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_deployment_results_deployed_at ON deployment_results(deployed_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_deployment_results_candidate ON deployment_results(candidate_id)")
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
        def _op():
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
        self._with_retry(_op)

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
                "INSERT INTO review_items (id, candidate_id, status, created_at, expires_at, locked_by, locked_at, telegram_message_id) VALUES (?, ?, 'pending', ?, ?, NULL, NULL, NULL)",
                (review_id, candidate_id, _utc_now_iso(), expires_at),
            )

    def set_review_telegram_message_id(self, review_id: str, message_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE review_items SET telegram_message_id = ? WHERE id = ?",
                (message_id, review_id),
            )

    def get_review_item(self, review_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM review_items WHERE id = ?",
                (review_id,),
            ).fetchone()

    def lock_review_item(self, review_id: str, locked_by: str) -> bool:
        now = _utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, expires_at FROM review_items WHERE id = ?",
                (review_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                return False
            if row["expires_at"] < now:
                return False
            cur = conn.execute(
                "UPDATE review_items SET status = 'deploying', locked_by = ?, locked_at = ? WHERE id = ? AND status = 'pending' AND expires_at >= ?",
                (locked_by, now, review_id, now),
            )
        return cur.rowcount == 1

    def reject_review_item(self, review_id: str, locked_by: str) -> bool:
        now = _utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, expires_at FROM review_items WHERE id = ?",
                (review_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                return False
            cur = conn.execute(
                "UPDATE review_items SET status = 'rejected', locked_by = ?, locked_at = ? WHERE id = ? AND status = 'pending'",
                (locked_by, now, review_id),
            )
        return cur.rowcount == 1

    def list_pending_reviews(self) -> list[sqlite3.Row]:
        now = _utc_now_iso()
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT r.*, s.raw_text, s.source, s.metadata_json,
                       d.score, d.reason_codes
                FROM review_items r
                JOIN signal_candidates s ON s.id = r.candidate_id
                LEFT JOIN candidate_decisions d ON d.candidate_id = r.candidate_id
                WHERE r.status = 'pending' AND r.expires_at >= ?
                ORDER BY r.created_at DESC
                """,
                (now,),
            ).fetchall()

    def save_deployment_result(
        self,
        result_id: str,
        candidate_id: str,
        status: str,
        deployed_at: str,
        tx_hash: str | None = None,
        contract_address: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deployment_results
                    (id, candidate_id, status, tx_hash, contract_address, error_code, error_message, latency_ms, deployed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    tx_hash = excluded.tx_hash,
                    contract_address = excluded.contract_address,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    latency_ms = excluded.latency_ms
                """,
                (result_id, candidate_id, status, tx_hash, contract_address, error_code, error_message, latency_ms, deployed_at),
            )

    def list_recent_deployments(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM deployment_results ORDER BY deployed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def get_latest_deployment_for_candidate(self, candidate_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM deployment_results
                WHERE candidate_id = ?
                ORDER BY deployed_at DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()

    def save_reward_claim_result(
        self,
        result_id: str,
        token_address: str,
        status: str,
        claimed_at: str,
        tx_hash: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reward_claim_results
                    (id, token_address, status, tx_hash, error_code, error_message, claimed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    tx_hash = excluded.tx_hash,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    claimed_at = excluded.claimed_at
                """,
                (result_id, token_address, status, tx_hash, error_code, error_message, claimed_at),
            )

    def get_stats(self) -> dict:
        with self._connect() as conn:
            now = _utc_now_iso()
            pending = conn.execute(
                "SELECT COUNT(*) FROM review_items WHERE status = 'pending' AND expires_at >= ?", (now,)
            ).fetchone()[0]
            total_candidates = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
            deployed = conn.execute(
                "SELECT COUNT(*) FROM deployment_results WHERE status = 'deploy_success'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM deployment_results WHERE status = 'deploy_failed'"
            ).fetchone()[0]
            rejected = conn.execute(
                "SELECT COUNT(*) FROM review_items WHERE status = 'rejected'"
            ).fetchone()[0]
        return {
            "pending_reviews": pending,
            "total_candidates": total_candidates,
            "deployed": deployed,
            "deploy_failed": failed,
            "rejected": rejected,
        }
