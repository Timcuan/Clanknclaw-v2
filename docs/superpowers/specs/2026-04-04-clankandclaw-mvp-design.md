# Clank&Claw MVP Design

## Objective

Build the first working Clank&Claw MVP as a single Python service that detects promising Base token deploy signals, routes them through a fast deterministic scoring pipeline, asks for Telegram approval, and executes a real deploy through the first deployer implementation.

This MVP is platform-agnostic by design but implements only the Clanker deploy path first. The Bankr deployer is intentionally deferred to the next spec so the first end-to-end path can be stabilized before adding a second onchain integration.

The system is optimized for speed, low operator friction, and future extensibility. It is not a trading bot. Its purpose is to catch deploy moments early and create tax-enabled token deployments with burner-wallet-oriented configuration.

## Scope

### In Scope

- Real-time signal ingestion from X via `twscrape`
- Real-time signal ingestion from GMGN Base new-launch polling
- Shared normalization into one internal candidate model
- Deterministic quick filtering
- Lightweight scoring with explicit reason codes
- Platform routing abstraction with first implementation targeting Clanker
- Telegram-based operator approval flow
- Metadata preparation after approval
- Image fetch and Pinata upload
- Configurable tax and spoofing flags in deploy payload
- Clanker deploy execution
- SQLite-backed persistence for candidates, review queue, and deploy history
- Notifications for review, approval result, success, and failure

### Out of Scope

- Bankr deploy execution
- FastAPI dashboard
- Revenue accounting
- PostgreSQL migration
- Multi-wallet or multi-deploy fanout
- Advanced reputation systems
- Heavy LLM dependence
- Fully automated no-approval deployment

## Product Constraints

- Python 3.11+
- Async-first runtime
- One clean main pipeline, not multiple competing execution paths
- Telegram approval is required before any real deploy in the MVP
- High-confidence candidates become priority reviews, not auto-deploys
- LLM usage is optional and sparse, only for extraction fallback
- SQLite is the only persistence layer in this phase

## Architecture

The MVP runs as one async Python application with a `Supervisor` that owns a small set of focused workers. The service is operationally simple but structurally separated so each unit has one clear responsibility.

Primary workers:

- `XDetector`
- `GMGNDetector`
- `CandidatePipeline`
- `TelegramReviewWorker`
- `ClankerDeployWorker`

The detectors ingest source events independently and normalize them into a shared internal format. The pipeline performs dedupe, filtering, scoring, routing, and review creation. Telegram acts as the operator surface for approval or rejection. Only approved items continue to metadata preparation and deploy execution. The Clanker deployer is the only live deploy implementation in this phase.

This architecture keeps the MVP fast while preserving clean boundaries:

- detectors do ingestion and normalization only
- pipeline does decision-making only
- router selects platform and review behavior
- deployer accepts already-approved deploy requests
- Telegram handlers do operator interaction only

## Execution Flow

1. `XDetector` polls mentions and keyword matches from X.
2. `GMGNDetector` polls Base new-launch signals.
3. Both detectors normalize events into `SignalCandidate`.
4. `CandidatePipeline` applies dedupe and cooldown checks.
5. Candidates pass through deterministic quick filters.
6. Remaining candidates are scored with lightweight heuristics.
7. Router assigns a recommended platform and review priority.
8. The pipeline writes the result to SQLite as `skip`, `review`, or `priority_review`.
9. `TelegramReviewWorker` pushes review items to Telegram.
10. Operator chooses approve or reject.
11. On approval, the pipeline performs final extraction, image fetch, IPFS upload, metadata assembly, config validation, and deploy preflight.
12. `ClankerDeployWorker` submits the deploy transaction.
13. Result is stored in SQLite and sent back to Telegram.

The expensive or irreversible work happens only after operator approval. This preserves speed in the decision path while reducing waste on low-quality or rejected candidates.

## Decision Model

The decision pipeline is explicit and deterministic. A candidate must always end up in one of these states:

- `skip`
- `review`
- `priority_review`
- `approved`
- `rejected`
- `expired`
- `deploying`
- `deploy_success`
- `deploy_failed`

`priority_review` means the candidate should be presented faster and more prominently to the operator. It does not bypass approval.

The pipeline must also record reason codes for both accepted and rejected candidates so operator feedback and future tuning remain audit-friendly.

## Core Components

### Runtime

- `main.py`
- `core/supervisor.py`

`main.py` loads configuration, builds dependencies, initializes persistence, and starts the supervisor. `Supervisor` manages worker startup, cancellation, restart policy, and shared health state.

### Detection

- `core/detectors/x_detector.py`
- `core/detectors/gmgn_detector.py`

Each detector is source-specific and output-compatible. Their job ends at normalized candidate creation. They do not score, route, or decide deployability.

### Decision Pipeline

- `core/pipeline.py`
- `core/filter.py`
- `core/scorer.py`
- `core/router.py`
- `core/review_queue.py`

`filter.py` contains deterministic rule-based rejection and allow rules. `scorer.py` produces a numeric score plus reason codes. `router.py` selects the recommended deploy target and review priority. `review_queue.py` manages pending operator actions and approval locking.

### Deployers

- `deployers/base.py`
- `deployers/clanker.py`

`BaseDeployer` defines the contract for all deployers. The MVP requires at minimum:

- `prepare(deploy_request)`
- `preflight(deploy_request)`
- `deploy(deploy_request)`

Only `ClankerDeployer` is implemented in this phase.

### Utilities

- `utils/extraction.py`
- `utils/image_fetcher.py`
- `utils/ipfs.py`
- `utils/llm.py`
- `utils/helpers.py`

`extraction.py` is regex-first and only falls back to a lightweight LLM call if deterministic extraction cannot produce a safe token name or symbol. `image_fetcher.py` resolves and validates the image source. `ipfs.py` uploads assets and metadata to Pinata.

### Telegram

- `telegram/bot.py`

Telegram is the operator control surface. It must support:

- approve
- reject
- queue display
- item status display
- deploy success notification
- deploy failure notification

### Persistence

- `database/manager.py`

SQLite is the system of record for candidate lifecycle, dedupe fingerprints, review items, and deploy history.

## Data Model

### `SignalCandidate`

Normalized event produced by any detector.

Fields:

- `id`
- `source`
- `source_event_id`
- `observed_at`
- `raw_text`
- `author_handle`
- `context_url`
- `suggested_name`
- `suggested_symbol`
- `fingerprint`
- `metadata`

### `ScoredCandidate`

Pipeline output after filter and scorer evaluation.

Fields:

- `candidate_id`
- `score`
- `decision`
- `reason_codes`
- `recommended_platform`
- `review_priority`

### `ReviewItem`

Pending operator decision.

Fields:

- `id`
- `candidate_id`
- `status`
- `created_at`
- `expires_at`
- `locked_by`
- `locked_at`
- `telegram_message_id`

### `DeployRequest`

Final deploy payload for a chosen deployer.

Fields:

- `candidate_id`
- `platform`
- `token_name`
- `token_symbol`
- `image_uri`
- `metadata_uri`
- `tax_bps`
- `tax_recipient`
- `token_admin_enabled`
- `token_reward_enabled`
- `burner_wallet`

### `DeployResult`

Stored outcome from deploy execution.

Fields:

- `deploy_request_id`
- `status`
- `tx_hash`
- `contract_address`
- `latency_ms`
- `error_code`
- `error_message`
- `completed_at`

## Filtering and Scoring

The MVP uses a tiered but mostly deterministic decision engine.

### Quick Filter

Fast reject rules should cover:

- missing deploy intent keywords
- malformed or irrelevant content
- obvious duplicate or replayed signals
- expired freshness window
- source-specific spam patterns

The filter layer must avoid network calls and avoid LLM usage.

### Scoring

The scorer should remain lightweight and explainable. It combines source quality, keyword strength, freshness, extraction confidence, and contextual hints into one numeric score with reason codes.

### LLM Usage

LLM is not part of default scoring. It is permitted only as a narrow fallback for token name and symbol extraction when regex and deterministic parsing fail. The fallback must be cheap, bounded, and optional by config.

## Routing

The router returns both:

- `recommended_platform`
- `review_priority`

For the MVP:

- all real deploys route to `clanker`
- the router contract remains generic for future `bankr`
- `priority_review` is used for high-confidence candidates
- `review` is used for medium-confidence candidates
- low-confidence candidates are skipped

## Approval Model

The approval mode is hybrid:

- high-confidence candidates become fast `priority_review` items
- medium-confidence candidates become normal `review` items
- low-confidence candidates are skipped

Every real deploy still requires explicit Telegram approval from the operator.

The review queue must support:

- per-item approve or reject
- expiration after a configured window
- lock protection to prevent duplicate approval handling
- queue browsing for non-priority items

## Deploy Preparation

After approval and before onchain submission, the system performs a narrow preparation phase:

1. finalize token name and symbol extraction
2. fetch the selected image
3. sanitize and validate image input
4. upload image and metadata to Pinata
5. build deploy payload with tax and spoofing flags
6. run deployer preflight

The preparation phase happens only after approval so the hot path before operator review remains short.

## Safety Rules

The MVP safety layer is intentionally balanced rather than defensive-heavy.

Required protections:

- startup config validation
- dedupe fingerprinting
- cooldown windows
- review-item locking
- payload validation before deploy
- tax configuration validation
- spoofing flag validation
- bounded retries for network-bound steps
- timeout handling for RPC and HTTP operations

Explicitly excluded in this phase:

- advanced reputation scoring
- full honeypot intelligence
- autonomous approval bypass

## Persistence Design

SQLite tables should minimally cover:

- `signal_candidates`
- `candidate_decisions`
- `review_items`
- `deploy_requests`
- `deploy_results`
- `dedupe_fingerprints`

Persistence requirements:

- candidate lifecycle must be queryable end-to-end
- approval actions must be idempotent
- deploy outcomes must be auditable
- dedupe and cooldown checks must survive process restart

## Configuration

Configuration should be split between static structure in `config.yaml` and secrets in `.env`.

Expected configuration domains:

- RPC endpoint
- Pinata credentials
- Telegram bot token and chat rules
- X scraping account/session config
- GMGN polling intervals
- scoring thresholds
- review expiration
- tax basis points
- tax recipient wallet
- burner wallet
- deployer-specific contract settings

The process should fail fast on startup if required deploy or notification secrets are missing.

## Logging and Notifications

The system should produce structured logs with candidate IDs and deploy request IDs included whenever possible.

Telegram notifications should cover:

- new review item
- priority review item
- candidate approved
- candidate rejected
- deploy success with transaction details
- deploy failure with concise reason code

## Testing Strategy

### Unit Tests

- filter rules
- scorer heuristics
- router decisions
- extraction behavior
- dedupe logic
- review queue locking

### Contract Tests

- `BaseDeployer` interface compatibility
- `ClankerDeployer` payload mapping and preflight behavior

### Integration Tests

- detector output normalization into `SignalCandidate`
- candidate pipeline state transitions
- approval-to-deploy request flow
- persistence of deploy result states

### Failure Tests

- RPC timeout
- Pinata upload failure
- Telegram callback race
- duplicate approval attempts

The MVP does not require live-chain end-to-end automation in CI. A manual smoke test is sufficient for the first deploy path.

## Folder Structure

The codebase should start with this structure:

```text
clankandclaw/
├── main.py
├── config.yaml
├── .env
├── requirements.txt
├── core/
│   ├── supervisor.py
│   ├── pipeline.py
│   ├── filter.py
│   ├── scorer.py
│   ├── router.py
│   ├── review_queue.py
│   └── detectors/
│       ├── x_detector.py
│       └── gmgn_detector.py
├── deployers/
│   ├── base.py
│   └── clanker.py
├── utils/
│   ├── extraction.py
│   ├── image_fetcher.py
│   ├── ipfs.py
│   ├── llm.py
│   └── helpers.py
├── telegram/
│   └── bot.py
├── database/
│   └── manager.py
├── models/
│   └── token.py
└── logs/
```

## Implementation Boundaries

This spec covers one implementation plan only if the next execution phase remains limited to:

- one running service
- one real deployer implementation
- one approval surface
- one persistence backend
- no dashboard
- no revenue subsystem
- no multi-wallet fanout

If Bankr deployment, dashboarding, or multi-deploy is added to the same implementation plan, the scope should be split.

## Success Criteria

The MVP is successful when all of the following are true:

- it ingests signals from X and GMGN into one candidate pipeline
- it deterministically filters and scores candidates
- it sends review items to Telegram with priority separation
- approved items are turned into valid deploy requests
- the Clanker deploy path executes successfully
- candidate and deploy history are queryable from SQLite
- duplicate approvals and duplicate deploy attempts are blocked

## Deferred Follow-Up Specs

The next design documents should cover:

- Bankr deployer implementation
- multi-deploy and multi-wallet fanout
- operator dashboard
- revenue and post-deploy analytics
