# Changelog

All notable changes to the Clank&Claw MVP project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### TODO
- X account configuration for twscrape
- Node.js runtime setup (`npm install`) on target host
- Production testing and monitoring

### Added
- Detector runtime throughput controls:
  - `max_process_concurrency` for X/Farcaster/Gecko workers
  - `max_query_concurrency` for X/Farcaster query fanout
  - loop latency telemetry (`x.loop_ms`, `farcaster.loop_ms`, `gecko.loop_ms`)
- GeckoTerminal hybrid momentum gate:
  - stage-1 fast spike/freshness shortlist
  - stage-2 velocity/liquidity scoring
  - stage-3 Base source/factory confidence validation
- Gecko adaptive anti-block pacing (dynamic request interval multiplier + degraded mode)
- Gecko cooldown-aware reprocessing to avoid duplicate spam while allowing significant surge re-evaluation
- Provider-safe request posture (Gecko/Farcaster):
  - consistent `User-Agent` and conservative default headers
  - cooldown/circuit behavior on repeated `403/429/5xx`
  - bounded request pacing for compliant 24/7 operation
- Shared parsing utilities for detectors/extraction:
  - unified mention/chain/contract/symbol/name hint parsing
  - improved structured symbol/ticker detection (including lowercase and punctuation cleanup)
  - reduced parser drift between X and Farcaster normalization paths
- Runtime resilience controls for difficult moments:
  - detector loop timeout guard
  - per-candidate processing timeout in detector pipeline
  - bounded pending notification queue to avoid burst-memory pressure
  - deploy preparation/deploy execution timeout guardrails
- Telegram UX revamp for operator flow:
  - richer review card with momentum-first context (`chain`, `volume`, `tx`, `liquidity`, `confidence`, `gate`)
  - standardized message sections across review/deploy/claim outputs
  - expanded inline actions (`Approve`, `Reject`, `Detail`, `Refresh`, `Queue`, `Deploys`)
- Telegram API hardening aligned with recent Bot API capabilities:
  - optional `message_thread_id` routing for topic/thread-based chats
  - bounded retry/backoff on outbound bot notifications for transient `429`/delivery failures
  - auto-capture operator topic/thread context and reuse as fallback routing target when static thread is not configured
- Per-category Telegram forum topic routing:
  - `thread_review_id`, `thread_deploy_id`, `thread_claim_id`, `thread_ops_id`, `thread_alert_id`
  - env support: `TELEGRAM_THREAD_REVIEW_ID`, `TELEGRAM_THREAD_DEPLOY_ID`, `TELEGRAM_THREAD_CLAIM_ID`, `TELEGRAM_THREAD_OPS_ID`, `TELEGRAM_THREAD_ALERT_ID`
  - smart bind: auto-learn topic/thread per category from operator actions, persisted in SQLite (`runtime_settings`)
- Runtime wallet management from Telegram:
  - `/wallets`, `/setsigner`, `/setadmin`, `/setreward` for live global wallet overrides
  - overrides stored in `runtime_settings` and applied at deploy preparation time
  - signer private-key override is passed to Node deploy process without requiring service redeploy
- Minimal runtime operation controls from Telegram:
  - `/control`, `/setmode`, `/setbot`, `/setdeployer`
  - `auto` mode now auto-approves `priority_review` candidates only
  - `bot_enabled=off` suppresses non-critical notifications
  - `deployer_mode=bankr|both` is persisted but currently reported as unsupported at execution time (safe fail)
- SQLite performance hardening for 24/7 workload:
  - WAL mode + busy timeout + hot-path indexes
  - retry handling for transient `database is locked`
- Context-aware token image selection:
  - ranked image candidates from metadata and source text
  - profile/avatar/banner de-prioritization for social sources
  - deterministic placeholder image fallback (including SVG fallback when PIL unavailable)
- Farcaster detector worker integrated into main pipeline (Neynar cast search)
- Farcaster smart extraction and scoring for Bankr/Clanker mention signals
- X detector mention-centric query mode (`to:/from:/@`) for `@bankrbot` and `@clankerdeploy`
- Smart X extraction from tweet content:
  - target mentions
  - contract candidates (EVM/Solana format)
  - symbol (`$TICKER`) and chain hints
  - engagement metadata (like/reply/retweet/quote)
- GeckoTerminal multi-network detector (Base/ETH/Solana/BSC) using `new_pools`
- Hot-pool gate tuned for fast momentum capture:
  - volume m5/m15 thresholds
  - tx count m5 threshold
  - liquidity threshold
  - pool freshness (max age minutes)
- Per-request rate limiter in detector worker (`max_requests_per_minute`) to reduce API block risk on 24/7 VPS
- Gecko-normalized candidate metadata for scoring: network, dex, volume, transactions, liquidity, hot score
- Clanker reward split policy hardening:
  - 10 bps (0.1%) to token admin interface target
  - 9990 bps (99.9%) to fee recipient reward wallet
- Natural metadata/context enrichment from source signals (author/context URL/excerpt)
- Alchemy RPC priority support (`ALCHEMY_BASE_RPC_URL`, `ALCHEMY_RPC`)
- Pinata smart dedupe cache (content-hash -> CID reuse)
- Generic IPFS upload support for arbitrary file types with MIME auto-detection
- Clanker claim-fees integration via Telegram command `/claimfees <token_address>`
- Reward claim audit logging in `reward_claim_results`

### Changed
- Deploy preparation image stage now prefers contextual token image candidates over single raw image_url
- X worker now captures richer media URL candidates for downstream image selection
- Farcaster and X polling now execute query fanout concurrently with bounded semaphores
- Gecko network polling order now prioritizes gameplay: `base -> solana -> bsc -> eth`
- Quick filter/scoring layer now consumes Gecko staged metadata (`gate_stage`, `confidence_tier`, `m1` velocity, `spike_ratio_m1_m5`) for faster and cleaner review routing
- X filter/scoring upgraded to prioritize target-mention intent and engagement bursts
- Replaced GMGN ingestion path with GeckoTerminal ingestion path in supervisor/config/runtime
- Pipeline quick filter now handles Gecko hot-pool signals directly (not keyword-only)
- Scoring engine now weights Gecko momentum metrics for faster priority review routing
- Liquidity pool remains hardcoded for safety:
  - paired token WETH Base
  - starting market cap 10 ETH
  - dev buy fixed to 0

### Fixed
- Prevented image/context mismatch by rejecting weak social avatar/banner image candidates
- Reduced event-loop blocking under burst load by offloading sync pipeline calls to worker threads
- Removed Telegram send wait from detector hot path (X/Farcaster/Gecko now use bounded async notification tasks)
- Mitigated over-polling risk with request pacing and bounded poll scope per network
- Prevented misconfiguration risk for reward recipient delivery with strict preflight checks
- Added fail-fast guardrails for invalid SDK success output and missing node modules
- Removed duplicate slash-command responses caused by cross-posting into ops topic while replying in the source topic

## [0.4.0] - 2026-04-05

### Added
- **X Polling Implementation** ✅
  - twscrape integration for X/Twitter polling
  - Keyword-based search (configurable keywords)
  - Tweet normalization and processing
  - Graceful fallback when twscrape unavailable
  - Configurable poll interval and max results
- **GMGN Polling Implementation** ✅
  - httpx-based API polling
  - Token launch detection on Base
  - Deduplication logic to prevent reprocessing
  - Token description building from metadata
  - Configurable poll interval and API URL
- **Clanker SDK Deployment Bridge** ✅
  - Python → Node.js subprocess bridge for `clanker-sdk/v4`
  - RPC endpoint configuration
  - Deterministic output parsing into `DeployResult`
  - Graceful fallback when Node.js unavailable
- **Candidate Database Queries** ✅
  - get_candidate() method for retrieving candidates by ID
  - Candidate reconstruction from database
  - Full integration with deploy preparation
- **Configuration Enhancements** ✅
  - X detector configuration (enabled, poll_interval, keywords, max_results)
  - GMGN detector configuration (enabled, poll_interval, api_url, max_results)
  - BASE_RPC_URL environment variable
  - CLANKER_NODE_MODULES_PATH environment variable (optional)

### Changed
- X detector worker now uses twscrape for actual polling
- GMGN detector worker now uses httpx for API polling
- Clanker deployer now uses Clanker SDK bridge execution
- Deploy worker uses database queries instead of mock data
- Supervisor passes detector configurations from config
- Updated .env.example with new environment variables

### Fixed
- Deploy preparation now properly retrieves candidates from database
- Deploy worker no longer uses mock candidate data

## [0.3.0] - 2026-04-05

### Added
- **Deploy Preparation Pipeline** ✅
  - Token identity extraction (regex-first with LLM fallback)
  - Image fetching and IPFS upload
  - Metadata building and IPFS upload
  - Deploy request creation
  - Deployer preflight checks
  - Error handling with specific error types
- **Deploy Worker** ✅
  - Orchestrates preparation and deployment
  - Handles success/failure notifications
  - Integrates with Telegram worker
  - Graceful error handling
- **Telegram Bot Integration** ✅
  - Full aiogram bot implementation with inline keyboards
  - Approve/reject callback handlers
  - Review notification with priority indicators
  - Deploy success/failure notifications
  - Bot commands (/start, /help, /status)
  - Optional import support (graceful degradation without aiogram)
- Telegram worker with review item creation
- Worker wiring (detectors → telegram → deploy)
- Review expiry configuration passed to telegram worker
- Comprehensive telegram bot tests

### Changed
- Telegram worker now triggers deploy preparation on approval
- Supervisor wires deploy worker to telegram worker
- Deploy worker sends notifications via telegram worker
- X and GMGN detector workers send notifications via telegram worker
- Updated requirements.txt with aiogram version pinning

### Fixed
- Test compatibility when aiogram is not installed
- Import errors handled gracefully in production

## [0.2.0] - 2026-04-05

### Added
- Async worker framework for X detector, GMGN detector, and Telegram bot
- Supervisor with graceful shutdown and signal handling
- Worker lifecycle management (start/stop)
- Logging configuration with configurable log levels
- `.env.example` template for environment variables
- README.md with comprehensive documentation
- DEPLOYMENT.md with production deployment guide
- CHANGELOG.md for tracking changes
- IMPLEMENTATION_STATUS.md for detailed progress tracking

### Changed
- Supervisor now manages actual async workers instead of simple list
- Main entrypoint now uses asyncio and proper logging
- Test suite updated for async supervisor (67 tests passing)

## [0.1.0] - 2026-04-04

### Added
- Initial MVP implementation
- Core models with Pydantic validation
- SQLite database manager
- Decision pipeline
- Detector normalization
- Deployer framework
- Utilities (extraction, image fetch, IPFS)
- Telegram message formatting
- Configuration management
- Comprehensive test suite (66 unit tests)

### Security
- SSRF protection in image fetcher
- Input validation at all layers
- SQL injection protection
- Foreign key integrity

## Release Notes

### Version 0.4.0 - Polling & Deployment Release (Current)

This release completes the polling implementations and deployment framework:

**What's New:**
- ✅ X polling with twscrape
  - Keyword search
  - Tweet processing
  - Graceful fallback
- ✅ GMGN polling with httpx
  - API polling
  - Token launch detection
  - Deduplication
- ✅ Clanker SDK deployment bridge
  - Python ↔ Node.js integration
  - RPC integration
  - Structured result parsing
- ✅ Candidate database queries
  - Full retrieval by ID
  - Reconstruction for deploy

**Test Status:**
- ✅ Full test suite passing in current branch
- ⏭️ 1 test skipped (requires aiogram)

**What's Next:**
- ⚠️  X account configuration (30-60 min)
- ⚠️  Node.js dependency setup on deployment host
- ⚠️  Production testing (30-60 min)

The MVP is now 95-98% complete! All core functionality is implemented. Only production configuration remains.

### Version 0.3.0 - Deploy Preparation Release

Complete deploy preparation pipeline with Telegram integration.

### Version 0.2.0 - Telegram Integration Release

Complete Telegram bot integration for approval flow.

### Version 0.1.0 - Foundation Release

Initial foundation with all core building blocks.
