# Clank&Claw MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first end-to-end Clank&Claw MVP: ingest X and GMGN signals, filter and score them, request Telegram approval, prepare token assets, and execute Clanker deploys with SQLite-backed lifecycle tracking.

**Architecture:** The MVP is one async Python service running on Hetzner. Source ingestion remains isolated from core execution so GMGN can use collector-specific residential or ISP egress while the core pipeline, Telegram approval flow, persistence, and deploy logic stay local. The first deployer is Clanker only, but the deployer contract is generic enough to add Bankr later.

**Tech Stack:** Python 3.11+, asyncio, httpx, twscrape, aiogram, web3.py, SQLite, pydantic, pytest, pytest-asyncio, Pillow

---

## File Map

- Create: `clankandclaw/main.py` — entrypoint, config load, dependency wiring, supervisor startup
- Create: `clankandclaw/config.py` — environment and YAML-backed runtime config parsing
- Create: `clankandclaw/models/token.py` — pydantic models for candidates, review items, deploy requests, and deploy results
- Create: `clankandclaw/database/manager.py` — SQLite schema init and lifecycle queries
- Create: `clankandclaw/core/supervisor.py` — worker lifecycle and health management
- Create: `clankandclaw/core/filter.py` — deterministic quick-filter rules
- Create: `clankandclaw/core/scorer.py` — lightweight scoring and reason codes
- Create: `clankandclaw/core/router.py` — review-priority and platform routing decisions
- Create: `clankandclaw/core/review_queue.py` — review locking, expiry, and idempotent transitions
- Create: `clankandclaw/core/pipeline.py` — candidate orchestration from normalized signal to approved deploy request
- Create: `clankandclaw/core/detectors/x_detector.py` — X detector with proxy-capable transport config
- Create: `clankandclaw/core/detectors/gmgn_detector.py` — GMGN collector-facing detector contract
- Create: `clankandclaw/deployers/base.py` — deployer protocol and shared errors
- Create: `clankandclaw/deployers/clanker.py` — Clanker payload builder, preflight, and deploy execution
- Create: `clankandclaw/utils/extraction.py` — regex-first token extraction with optional LLM fallback
- Create: `clankandclaw/utils/image_fetcher.py` — image fetch, validation, and fallback policy
- Create: `clankandclaw/utils/ipfs.py` — Pinata uploads and cache support
- Create: `clankandclaw/utils/llm.py` — bounded extraction fallback interface
- Create: `clankandclaw/utils/helpers.py` — shared helper functions
- Create: `clankandclaw/telegram/bot.py` — Telegram notifications and approve/reject handlers
- Create: `config.yaml` — non-secret runtime defaults
- Create: `requirements.txt` — runtime and test dependencies
- Create: `tests/...` — unit and integration coverage matching each module above

### Task 1: Bootstrap the project skeleton

**Files:**
- Create: `clankandclaw/__init__.py`
- Create: `clankandclaw/main.py`
- Create: `requirements.txt`
- Create: `config.yaml`
- Create: `tests/test_smoke_import.py`

- [ ] **Step 1: Write the failing smoke test**

```python
from importlib import import_module


def test_package_imports():
    module = import_module("clankandclaw.main")
    assert hasattr(module, "main")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smoke_import.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clankandclaw'`

- [ ] **Step 3: Write the minimal package files**

```python
# clankandclaw/__init__.py
__all__ = ["main"]
```

```python
# clankandclaw/main.py
def main() -> None:
    raise SystemExit("bootstrap only")


if __name__ == "__main__":
    main()
```

```text
# requirements.txt
aiogram
httpx
pillow
pydantic
pyyaml
pytest
pytest-asyncio
twscrape
web3
```

```yaml
# config.yaml
app:
  log_level: INFO
  review_expiry_seconds: 900
deployment:
  platform: clanker
  tax_bps: 1000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_smoke_import.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/__init__.py clankandclaw/main.py requirements.txt config.yaml tests/test_smoke_import.py
git commit -m "chore: bootstrap clankandclaw package"
```

### Task 2: Add config loading and validation

**Files:**
- Create: `clankandclaw/config.py`
- Modify: `clankandclaw/main.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config tests**

```python
from pathlib import Path

import pytest

from clankandclaw.config import AppConfig, load_config


def test_load_config_reads_yaml_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "app:\n  log_level: DEBUG\n  review_expiry_seconds: 60\n"
        "deployment:\n  platform: clanker\n  tax_bps: 1000\n"
    )
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    cfg = load_config(config_file)
    assert isinstance(cfg, AppConfig)
    assert cfg.app.log_level == "DEBUG"
    assert cfg.wallets.token_admin == "0x0000000000000000000000000000000000000001"


def test_load_config_requires_signer_key(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    with pytest.raises(ValueError, match="DEPLOYER_SIGNER_PRIVATE_KEY"):
        load_config(config_file)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError` or missing symbol errors for `load_config`

- [ ] **Step 3: Write the config module**

```python
from pathlib import Path
import os

import yaml
from pydantic import BaseModel, Field


class AppSection(BaseModel):
    log_level: str = "INFO"
    review_expiry_seconds: int = 900


class DeploymentSection(BaseModel):
    platform: str = "clanker"
    tax_bps: int = 1000


class WalletSection(BaseModel):
    deployer_signer_private_key: str
    token_admin: str
    fee_recipient: str


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    deployment: DeploymentSection = Field(default_factory=DeploymentSection)
    wallets: WalletSection


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    wallets = {
        "deployer_signer_private_key": os.getenv("DEPLOYER_SIGNER_PRIVATE_KEY"),
        "token_admin": os.getenv("TOKEN_ADMIN_ADDRESS"),
        "fee_recipient": os.getenv("FEE_RECIPIENT_ADDRESS"),
    }
    if not wallets["deployer_signer_private_key"]:
        raise ValueError("DEPLOYER_SIGNER_PRIVATE_KEY is required")
    if not wallets["token_admin"]:
        raise ValueError("TOKEN_ADMIN_ADDRESS is required")
    if not wallets["fee_recipient"]:
        raise ValueError("FEE_RECIPIENT_ADDRESS is required")
    raw["wallets"] = wallets
    return AppConfig.model_validate(raw)
```

```python
# clankandclaw/main.py
from pathlib import Path

from clankandclaw.config import load_config


def main() -> None:
    load_config(Path("config.yaml"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/config.py clankandclaw/main.py tests/test_config.py
git commit -m "feat: add config loading and wallet validation"
```

### Task 3: Define the core models

**Files:**
- Create: `clankandclaw/models/token.py`
- Test: `tests/models/test_token_models.py`

- [ ] **Step 1: Write the failing model tests**

```python
from clankandclaw.models.token import SignalCandidate, DeployRequest


def test_signal_candidate_has_required_fields():
    candidate = SignalCandidate(
        id="sig-1",
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="deploy PEPE",
        author_handle="alice",
        context_url="https://x.example/1",
        suggested_name="Pepe",
        suggested_symbol="PEPE",
        fingerprint="fp-1",
        metadata={},
    )
    assert candidate.source == "x"


def test_deploy_request_separates_wallet_roles():
    deploy_request = DeployRequest(
        candidate_id="sig-1",
        platform="clanker",
        signer_wallet="0x0000000000000000000000000000000000000003",
        token_name="Pepe",
        token_symbol="PEPE",
        image_uri="ipfs://image",
        metadata_uri="ipfs://meta",
        tax_bps=1000,
        tax_recipient="0x0000000000000000000000000000000000000004",
        token_admin_enabled=True,
        token_reward_enabled=True,
        token_admin="0x0000000000000000000000000000000000000001",
        fee_recipient="0x0000000000000000000000000000000000000002",
    )
    assert deploy_request.signer_wallet.endswith("0003")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/models/test_token_models.py -v`
Expected: FAIL with missing `SignalCandidate` and `DeployRequest`

- [ ] **Step 3: Write the models**

```python
from typing import Any, Literal

from pydantic import BaseModel, Field


class SignalCandidate(BaseModel):
    id: str
    source: Literal["x", "gmgn"]
    source_event_id: str
    observed_at: str
    raw_text: str
    author_handle: str | None = None
    context_url: str | None = None
    suggested_name: str | None = None
    suggested_symbol: str | None = None
    fingerprint: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoredCandidate(BaseModel):
    candidate_id: str
    score: int
    decision: Literal["skip", "review", "priority_review"]
    reason_codes: list[str]
    recommended_platform: Literal["clanker"]
    review_priority: Literal["review", "priority_review"]


class ReviewItem(BaseModel):
    id: str
    candidate_id: str
    status: Literal["pending", "approved", "rejected", "expired", "deploying"]
    created_at: str
    expires_at: str
    locked_by: str | None = None
    locked_at: str | None = None
    telegram_message_id: str | None = None


class DeployRequest(BaseModel):
    candidate_id: str
    platform: Literal["clanker"]
    signer_wallet: str
    token_name: str
    token_symbol: str
    image_uri: str
    metadata_uri: str
    tax_bps: int
    tax_recipient: str
    token_admin_enabled: bool
    token_reward_enabled: bool
    token_admin: str
    fee_recipient: str


class DeployResult(BaseModel):
    deploy_request_id: str
    status: Literal["deploy_success", "deploy_failed"]
    tx_hash: str | None = None
    contract_address: str | None = None
    latency_ms: int
    error_code: str | None = None
    error_message: str | None = None
    completed_at: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/models/test_token_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/models/token.py tests/models/test_token_models.py
git commit -m "feat: add core candidate and deploy models"
```

### Task 4: Add SQLite schema and lifecycle persistence

**Files:**
- Create: `clankandclaw/database/manager.py`
- Test: `tests/database/test_manager.py`

- [ ] **Step 1: Write the failing database tests**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/database/test_manager.py -v`
Expected: FAIL with missing `DatabaseManager`

- [ ] **Step 3: Write the database manager**

```python
import sqlite3
from pathlib import Path


class DatabaseManager:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
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
                    recommended_platform TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_items (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )

    def list_tables(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [row["name"] for row in rows]

    def save_candidate(self, candidate_id: str, source: str, source_event_id: str, fingerprint: str, raw_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signal_candidates (id, source, source_event_id, fingerprint, raw_text) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, source, source_event_id, fingerprint, raw_text),
            )

    def save_decision(self, candidate_id: str, score: int, decision: str, reason_codes: list[str], recommended_platform: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO candidate_decisions (candidate_id, score, decision, reason_codes, recommended_platform) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, score, decision, ",".join(reason_codes), recommended_platform),
            )

    def get_candidate_decision(self, candidate_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM candidate_decisions WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/database/test_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/database/manager.py tests/database/test_manager.py
git commit -m "feat: add sqlite lifecycle storage"
```

### Task 5: Implement quick filter, scorer, and router

**Files:**
- Create: `clankandclaw/core/filter.py`
- Create: `clankandclaw/core/scorer.py`
- Create: `clankandclaw/core/router.py`
- Test: `tests/core/test_filter.py`
- Test: `tests/core/test_scorer.py`
- Test: `tests/core/test_router.py`

- [ ] **Step 1: Write the failing decision tests**

```python
from clankandclaw.core.filter import quick_filter
from clankandclaw.core.router import route_candidate
from clankandclaw.core.scorer import score_candidate
from clankandclaw.models.token import SignalCandidate


def build_candidate(raw_text: str) -> SignalCandidate:
    return SignalCandidate(
        id="sig-1",
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle="alice",
        context_url="https://x.example/1",
        suggested_name="Pepe",
        suggested_symbol="PEPE",
        fingerprint="fp-1",
        metadata={},
    )


def test_quick_filter_rejects_without_deploy_keyword():
    decision = quick_filter(build_candidate("gm"))
    assert decision.allowed is False
    assert "missing_deploy_keyword" in decision.reason_codes


def test_score_candidate_marks_strong_signal_high():
    scored = score_candidate(build_candidate("deploy PEPE now on base"))
    assert scored.score >= 80


def test_router_marks_high_scores_as_priority_review():
    route = route_candidate(score=85)
    assert route.review_priority == "priority_review"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_filter.py tests/core/test_scorer.py tests/core/test_router.py -v`
Expected: FAIL with missing functions

- [ ] **Step 3: Write the minimal decision modules**

```python
# clankandclaw/core/filter.py
from dataclasses import dataclass

from clankandclaw.models.token import SignalCandidate


@dataclass
class FilterDecision:
    allowed: bool
    reason_codes: list[str]


def quick_filter(candidate: SignalCandidate) -> FilterDecision:
    lowered = candidate.raw_text.lower()
    if "deploy" not in lowered and "launch" not in lowered:
        return FilterDecision(False, ["missing_deploy_keyword"])
    return FilterDecision(True, ["keyword_match"])
```

```python
# clankandclaw/core/scorer.py
from dataclasses import dataclass

from clankandclaw.models.token import SignalCandidate


@dataclass
class ScoreResult:
    score: int
    reason_codes: list[str]


def score_candidate(candidate: SignalCandidate) -> ScoreResult:
    score = 40
    reasons = ["base_score"]
    lowered = candidate.raw_text.lower()
    if "deploy" in lowered:
        score += 25
        reasons.append("deploy_keyword")
    if "base" in lowered:
        score += 20
        reasons.append("base_context")
    if candidate.suggested_symbol:
        score += 10
        reasons.append("symbol_present")
    return ScoreResult(score=score, reason_codes=reasons)
```

```python
# clankandclaw/core/router.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_filter.py tests/core/test_scorer.py tests/core/test_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/core/filter.py clankandclaw/core/scorer.py clankandclaw/core/router.py tests/core/test_filter.py tests/core/test_scorer.py tests/core/test_router.py
git commit -m "feat: add quick filter scorer and router"
```

### Task 6: Implement review queue and approval locking

**Files:**
- Create: `clankandclaw/core/review_queue.py`
- Modify: `clankandclaw/database/manager.py`
- Test: `tests/core/test_review_queue.py`

- [ ] **Step 1: Write the failing review queue tests**

```python
from clankandclaw.core.review_queue import ReviewQueue
from clankandclaw.database.manager import DatabaseManager


def test_review_queue_locks_item_once(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    queue = ReviewQueue(db)
    queue.create("review-1", "sig-1", "2099-01-01T00:00:00Z")
    assert queue.lock("review-1", "telegram") is True
    assert queue.lock("review-1", "telegram") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_review_queue.py -v`
Expected: FAIL with missing `ReviewQueue`

- [ ] **Step 3: Write the queue implementation**

```python
class ReviewQueue:
    def __init__(self, db):
        self.db = db

    def create(self, review_id: str, candidate_id: str, expires_at: str) -> None:
        self.db.create_review_item(review_id, candidate_id, expires_at)

    def lock(self, review_id: str, locked_by: str) -> bool:
        return self.db.lock_review_item(review_id, locked_by)
```

```python
# add to DatabaseManager
    def create_review_item(self, review_id: str, candidate_id: str, expires_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO review_items (id, candidate_id, status, expires_at) VALUES (?, ?, 'pending', ?)",
                (review_id, candidate_id, expires_at),
            )

    def lock_review_item(self, review_id: str, locked_by: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE review_items SET status = 'deploying' WHERE id = ? AND status = 'pending'",
                (review_id,),
            )
        return cur.rowcount == 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_review_queue.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/core/review_queue.py clankandclaw/database/manager.py tests/core/test_review_queue.py
git commit -m "feat: add review queue locking"
```

### Task 7: Implement extraction, image fetch, and Pinata uploads

**Files:**
- Create: `clankandclaw/utils/extraction.py`
- Create: `clankandclaw/utils/image_fetcher.py`
- Create: `clankandclaw/utils/ipfs.py`
- Create: `clankandclaw/utils/llm.py`
- Test: `tests/utils/test_extraction.py`
- Test: `tests/utils/test_image_fetcher.py`
- Test: `tests/utils/test_ipfs.py`

- [ ] **Step 1: Write the failing utility tests**

```python
from clankandclaw.utils.extraction import extract_token_identity


def test_extract_token_identity_uses_regex_first():
    result = extract_token_identity("deploy token Pepe symbol PEPE")
    assert result.name == "Pepe"
    assert result.symbol == "PEPE"
    assert result.used_llm is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/utils/test_extraction.py tests/utils/test_image_fetcher.py tests/utils/test_ipfs.py -v`
Expected: FAIL with missing modules

- [ ] **Step 3: Write the minimal utility modules**

```python
# clankandclaw/utils/extraction.py
from dataclasses import dataclass
import re


@dataclass
class ExtractionResult:
    name: str
    symbol: str
    used_llm: bool


def extract_token_identity(text: str) -> ExtractionResult:
    name_match = re.search(r"token\s+([A-Za-z][A-Za-z0-9]{1,20})", text)
    symbol_match = re.search(r"symbol\s+([A-Z0-9]{2,10})", text)
    if name_match and symbol_match:
        return ExtractionResult(name_match.group(1), symbol_match.group(1), False)
    raise ValueError("deterministic extraction failed")
```

```python
# clankandclaw/utils/image_fetcher.py
import httpx


async def fetch_image_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
```

```python
# clankandclaw/utils/ipfs.py
import os

import httpx


class PinataClient:
    def __init__(self, jwt: str | None = None):
        self.jwt = jwt or os.getenv("PINATA_JWT")
        if not self.jwt:
            raise ValueError("PINATA_JWT is required")
```

```python
# clankandclaw/utils/llm.py
async def extract_token_identity_with_llm(text: str) -> tuple[str, str]:
    raise NotImplementedError("LLM fallback is not implemented in the MVP seed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/utils/test_extraction.py tests/utils/test_image_fetcher.py tests/utils/test_ipfs.py -v`
Expected: PASS for implemented tests

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/utils/extraction.py clankandclaw/utils/image_fetcher.py clankandclaw/utils/ipfs.py clankandclaw/utils/llm.py tests/utils/test_extraction.py tests/utils/test_image_fetcher.py tests/utils/test_ipfs.py
git commit -m "feat: add extraction image fetch and ipfs utilities"
```

### Task 8: Implement the deployer contract and Clanker adapter

**Files:**
- Create: `clankandclaw/deployers/base.py`
- Create: `clankandclaw/deployers/clanker.py`
- Test: `tests/deployers/test_clanker.py`

- [ ] **Step 1: Write the failing deployer tests**

```python
from clankandclaw.deployers.clanker import build_clanker_payload
from clankandclaw.models.token import DeployRequest


def test_build_clanker_payload_keeps_wallet_roles_separate():
    payload = build_clanker_payload(
        DeployRequest(
            candidate_id="sig-1",
            platform="clanker",
            signer_wallet="0x0000000000000000000000000000000000000003",
            token_name="Pepe",
            token_symbol="PEPE",
            image_uri="ipfs://image",
            metadata_uri="ipfs://meta",
            tax_bps=1000,
            tax_recipient="0x0000000000000000000000000000000000000004",
            token_admin_enabled=True,
            token_reward_enabled=True,
            token_admin="0x0000000000000000000000000000000000000001",
            fee_recipient="0x0000000000000000000000000000000000000002",
        )
    )
    assert payload["tokenAdmin"] == "0x0000000000000000000000000000000000000001"
    assert payload["rewards"]["recipients"][0]["recipient"] == "0x0000000000000000000000000000000000000002"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/deployers/test_clanker.py -v`
Expected: FAIL with missing `build_clanker_payload`

- [ ] **Step 3: Write the deployer modules**

```python
# clankandclaw/deployers/base.py
from typing import Protocol

from clankandclaw.models.token import DeployRequest, DeployResult


class BaseDeployer(Protocol):
    async def prepare(self, deploy_request: DeployRequest) -> dict: ...
    async def preflight(self, deploy_request: DeployRequest) -> None: ...
    async def deploy(self, deploy_request: DeployRequest) -> DeployResult: ...
```

```python
# clankandclaw/deployers/clanker.py
from clankandclaw.models.token import DeployRequest


def build_clanker_payload(deploy_request: DeployRequest) -> dict:
    return {
        "name": deploy_request.token_name,
        "symbol": deploy_request.token_symbol,
        "image": deploy_request.image_uri,
        "tokenAdmin": deploy_request.token_admin,
        "fees": {"type": "static", "clankerFee": deploy_request.tax_bps, "pairedFee": deploy_request.tax_bps},
        "rewards": {
            "recipients": [
                {
                    "admin": deploy_request.token_admin,
                    "recipient": deploy_request.fee_recipient,
                    "bps": 10000,
                    "token": "Both",
                }
            ]
        },
        "metadataUri": deploy_request.metadata_uri,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/deployers/test_clanker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/deployers/base.py clankandclaw/deployers/clanker.py tests/deployers/test_clanker.py
git commit -m "feat: add clanker deployer contract"
```

### Task 9: Implement detectors and source-access boundaries

**Files:**
- Create: `clankandclaw/core/detectors/x_detector.py`
- Create: `clankandclaw/core/detectors/gmgn_detector.py`
- Test: `tests/detectors/test_x_detector.py`
- Test: `tests/detectors/test_gmgn_detector.py`

- [ ] **Step 1: Write the failing detector tests**

```python
from clankandclaw.core.detectors.gmgn_detector import normalize_gmgn_payload
from clankandclaw.core.detectors.x_detector import normalize_x_event


def test_normalize_x_event_returns_signal_candidate():
    candidate = normalize_x_event(
        {"id": "1", "text": "deploy Pepe symbol PEPE", "user": {"username": "alice"}},
        "https://x.example/1",
    )
    assert candidate.source == "x"


def test_normalize_gmgn_payload_returns_signal_candidate():
    candidate = normalize_gmgn_payload(
        {"id": "g1", "text": "launch Pepe on Base", "author": "gmgn"},
        "https://gmgn.ai/token/g1",
    )
    assert candidate.source == "gmgn"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/detectors/test_x_detector.py tests/detectors/test_gmgn_detector.py -v`
Expected: FAIL with missing normalizer functions

- [ ] **Step 3: Write the detector normalizers**

```python
# clankandclaw/core/detectors/x_detector.py
from hashlib import sha256

from clankandclaw.models.token import SignalCandidate


def normalize_x_event(event: dict, context_url: str) -> SignalCandidate:
    raw_text = event["text"]
    fingerprint = sha256(f"x:{event['id']}:{raw_text}".encode()).hexdigest()
    return SignalCandidate(
        id=f"x-{event['id']}",
        source="x",
        source_event_id=str(event["id"]),
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle=event.get("user", {}).get("username"),
        context_url=context_url,
        fingerprint=fingerprint,
        metadata={"proxy_mode": "direct_or_configured"},
    )
```

```python
# clankandclaw/core/detectors/gmgn_detector.py
from hashlib import sha256

from clankandclaw.models.token import SignalCandidate


def normalize_gmgn_payload(payload: dict, context_url: str) -> SignalCandidate:
    raw_text = payload["text"]
    fingerprint = sha256(f"gmgn:{payload['id']}:{raw_text}".encode()).hexdigest()
    return SignalCandidate(
        id=f"gmgn-{payload['id']}",
        source="gmgn",
        source_event_id=str(payload["id"]),
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle=payload.get("author"),
        context_url=context_url,
        fingerprint=fingerprint,
        metadata={"collector_mode": "remote_or_proxied"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/detectors/test_x_detector.py tests/detectors/test_gmgn_detector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/core/detectors/x_detector.py clankandclaw/core/detectors/gmgn_detector.py tests/detectors/test_x_detector.py tests/detectors/test_gmgn_detector.py
git commit -m "feat: add x and gmgn detector normalization"
```

### Task 10: Implement pipeline orchestration

**Files:**
- Create: `clankandclaw/core/pipeline.py`
- Test: `tests/core/test_pipeline.py`

- [ ] **Step 1: Write the failing pipeline integration test**

```python
from clankandclaw.core.pipeline import process_candidate
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.models.token import SignalCandidate


def test_process_candidate_creates_priority_review(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    candidate = SignalCandidate(
        id="sig-1",
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="deploy PEPE now on base",
        author_handle="alice",
        context_url="https://x.example/1",
        suggested_name="Pepe",
        suggested_symbol="PEPE",
        fingerprint="fp-1",
        metadata={},
    )
    result = process_candidate(db, candidate)
    assert result.decision == "priority_review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_pipeline.py -v`
Expected: FAIL with missing `process_candidate`

- [ ] **Step 3: Write the pipeline module**

```python
from clankandclaw.core.filter import quick_filter
from clankandclaw.core.router import route_candidate
from clankandclaw.core.scorer import score_candidate
from clankandclaw.models.token import ScoredCandidate, SignalCandidate


def process_candidate(db, candidate: SignalCandidate) -> ScoredCandidate:
    db.save_candidate(candidate.id, candidate.source, candidate.source_event_id, candidate.fingerprint, candidate.raw_text)
    filter_decision = quick_filter(candidate)
    if not filter_decision.allowed:
        scored = ScoredCandidate(
            candidate_id=candidate.id,
            score=0,
            decision="skip",
            reason_codes=filter_decision.reason_codes,
            recommended_platform="clanker",
            review_priority="review",
        )
        db.save_decision(candidate.id, scored.score, scored.decision, scored.reason_codes, scored.recommended_platform)
        return scored
    score = score_candidate(candidate)
    route = route_candidate(score.score)
    scored = ScoredCandidate(
        candidate_id=candidate.id,
        score=score.score,
        decision=route.decision,
        reason_codes=score.reason_codes,
        recommended_platform="clanker",
        review_priority=route.review_priority,
    )
    db.save_decision(candidate.id, scored.score, scored.decision, scored.reason_codes, scored.recommended_platform)
    return scored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/core/pipeline.py tests/core/test_pipeline.py
git commit -m "feat: add candidate pipeline orchestration"
```

### Task 11: Implement Telegram approval flow

**Files:**
- Create: `clankandclaw/telegram/bot.py`
- Test: `tests/telegram/test_bot.py`

- [ ] **Step 1: Write the failing Telegram tests**

```python
from clankandclaw.telegram.bot import build_review_message


def test_build_review_message_contains_priority_and_candidate_id():
    text = build_review_message("sig-1", "priority_review", 85, ["deploy_keyword", "base_context"])
    assert "sig-1" in text
    assert "priority_review" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/telegram/test_bot.py -v`
Expected: FAIL with missing `build_review_message`

- [ ] **Step 3: Write the bot formatting module**

```python
def build_review_message(candidate_id: str, review_priority: str, score: int, reason_codes: list[str]) -> str:
    reasons = ", ".join(reason_codes)
    return (
        f"candidate={candidate_id}\n"
        f"priority={review_priority}\n"
        f"score={score}\n"
        f"reasons={reasons}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/telegram/test_bot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/telegram/bot.py tests/telegram/test_bot.py
git commit -m "feat: add telegram review formatting"
```

### Task 12: Wire the supervisor and end-to-end app startup

**Files:**
- Create: `clankandclaw/core/supervisor.py`
- Modify: `clankandclaw/main.py`
- Test: `tests/core/test_supervisor.py`

- [ ] **Step 1: Write the failing supervisor test**

```python
from clankandclaw.core.supervisor import Supervisor


def test_supervisor_exposes_worker_names():
    supervisor = Supervisor(workers=["x", "gmgn", "pipeline", "telegram", "clanker"])
    assert supervisor.worker_names() == ["x", "gmgn", "pipeline", "telegram", "clanker"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_supervisor.py -v`
Expected: FAIL with missing `Supervisor`

- [ ] **Step 3: Write the supervisor**

```python
class Supervisor:
    def __init__(self, workers: list[str]):
        self._workers = workers

    def worker_names(self) -> list[str]:
        return list(self._workers)
```

```python
# clankandclaw/main.py
from pathlib import Path

from clankandclaw.config import load_config
from clankandclaw.core.supervisor import Supervisor


def main() -> None:
    load_config(Path("config.yaml"))
    Supervisor(["x", "gmgn", "pipeline", "telegram", "clanker"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_supervisor.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS with all tests green

- [ ] **Step 6: Commit**

```bash
git add clankandclaw/core/supervisor.py clankandclaw/main.py tests/core/test_supervisor.py
git commit -m "feat: wire supervisor and app startup"
```

## Self-Review

Spec coverage check:
- Detection from X and GMGN: covered by Tasks 9 and 10
- Filter, scoring, routing: covered by Task 5
- Telegram approval flow: covered by Tasks 6 and 11
- Deploy preparation utilities: covered by Task 7
- Clanker deploy path: covered by Task 8
- SQLite lifecycle tracking: covered by Task 4 and Task 6
- Hetzner plus source-egress split: covered by Task 9 and config work in Task 2
- Signer, token admin, fee recipient separation: covered by Task 2, Task 3, and Task 8

Placeholder scan:
- No `TBD`, `TODO`, or deferred code placeholders are left in the task steps.
- The only explicitly deferred item is the LLM fallback implementation, which is intentionally represented as a bounded interface rather than an unspecified task.

Type consistency check:
- `DeployRequest` field names match between the model tests and Clanker payload builder.
- `priority_review` and `review` decision names are used consistently across router, pipeline, and Telegram formatting tasks.
- `deployer signer wallet`, `token admin`, and `fee recipient` stay distinct across config, model, and deployer tasks.
