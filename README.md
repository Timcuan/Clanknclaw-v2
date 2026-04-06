# Clank&Claw MVP

Automated token deployment system that detects promising Base token deploy signals, routes them through a deterministic scoring pipeline, requests Telegram approval, and executes Clanker deploys.

## Architecture

Single async Python service with:
- **X Detector**: Polls X/Twitter for deploy signals
- **Farcaster Detector**: Polls Farcaster casts (Bankr/Clanker mentions)
- **Gecko Detector**: Polls GeckoTerminal new pools (Base/ETH/Solana/BSC)
- **Pipeline**: Filters, scores, and routes candidates
- **Telegram Bot**: Operator approval interface
- **Clanker Deployer**: Executes approved deploys
- **SQLite**: Lifecycle tracking and persistence

## Requirements

- Python 3.11+
- SQLite 3
- Telegram Bot Token
- Pinata JWT for IPFS uploads
- Base RPC endpoint
- Deployer wallet with private key

## Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Copy environment template:
   ```bash
   cp .env.example .env
   ```

4. Configure environment variables in `.env`:
   - `DEPLOYER_SIGNER_PRIVATE_KEY`: Private key for signing deploy transactions
   - `TOKEN_ADMIN_ADDRESS`: Address for token admin role
   - `FEE_RECIPIENT_ADDRESS`: Address for fee collection
   - `TELEGRAM_BOT_TOKEN`: Telegram bot token from @BotFather
   - `TELEGRAM_CHAT_ID`: Telegram chat ID for notifications
   - `PINATA_JWT`: Pinata JWT for IPFS uploads

5. Review and adjust `config.yaml` as needed

## Configuration

### config.yaml

```yaml
app:
  log_level: INFO              # Logging level (DEBUG, INFO, WARNING, ERROR)
  review_expiry_seconds: 900   # Review item expiration (15 minutes)

x_detector:
  enabled: true                # Enable X/Twitter polling
  poll_interval: 30.0          # Polling interval in seconds
  keywords:                    # Keywords to search for
    - deploy
    - launch
  max_results: 20              # Max results per poll
  target_handles: ["bankrbot", "clankerdeploy"]  # Mention/focus handles
  query_terms: ["deploy", "launch", "contract", "ca", "token"]
  max_process_concurrency: 8
  max_query_concurrency: 3      # Parallel query tasks per loop

farcaster_detector:
  enabled: true
  poll_interval: 35.0
  api_url: "https://api.neynar.com/v2/farcaster/cast/search/"
  api_key: ""                 # prefer env: NEYNAR_API_KEY
  max_results: 20
  target_handles: ["bankr", "clanker"]
  query_terms: ["deploy", "launch", "contract", "ca", "token"]
  request_timeout_seconds: 20.0
  max_process_concurrency: 8
  max_query_concurrency: 2      # Parallel Neynar search queries per loop

gecko_detector:
  enabled: true                # Enable GeckoTerminal polling
  poll_interval: 25.0          # Polling interval in seconds
  api_base_url: "https://api.geckoterminal.com/api/v2"
  networks: ["base", "eth", "solana", "bsc"]  # Runtime priority: base -> solana -> bsc -> eth
  max_results: 20              # Max pools per network per poll
  max_pool_age_minutes: 120
  min_volume_m5_usd: 3000
  min_volume_m15_usd: 8000
  min_tx_count_m5: 12
  min_liquidity_usd: 12000
  max_requests_per_minute: 40  # Safety guard against API block
  base_target_sources: ["bankr", "doppler", "zora", "virtual", "uniswapv4", "clanker"]
  max_process_concurrency: 10

deployment:
  platform: clanker            # Deploy platform (only clanker in MVP)
  tax_bps: 1000               # Tax basis points (1000 = 10%)
```

Gecko runtime tuning:
- Hybrid staged gate for momentum capture:
  - Stage 1: fast spike + freshness shortlist
  - Stage 2: velocity + liquidity confidence
  - Stage 3: Base source/factory confidence validation
- Adaptive anti-block pacing: request interval auto-adjusts on `429/5xx` and recovers when healthy.
- Cooldown-aware reprocessing prevents spam while allowing significant momentum jumps.

### Environment Variables

See `.env.example` for all available environment variables.

**Required:**
- `DEPLOYER_SIGNER_PRIVATE_KEY`: Private key for signing transactions
- `TOKEN_ADMIN_ADDRESS`: Token admin wallet address
- `FEE_RECIPIENT_ADDRESS`: Fee recipient wallet address
- `TELEGRAM_BOT_TOKEN`: Telegram bot token
- `TELEGRAM_CHAT_ID`: Telegram chat ID for notifications
- `PINATA_JWT`: Pinata JWT for IPFS uploads
- `BASE_RPC_URL`: Base RPC endpoint (e.g., https://mainnet.base.org)
- Node.js runtime deps installed in this repo (`npm install`)

**Optional:**
- X/Twitter accounts configured via twscrape (see below)
- `NEYNAR_API_KEY` for Farcaster detector

### X/Twitter Polling Setup

To enable X polling, configure twscrape with authenticated accounts:

```bash
# Add accounts (username:password:email:email_password)
twscrape add_accounts accounts.txt username:password:email:email_password

# Or add interactively
twscrape add_account account1 username password email email_password

# Login to accounts
twscrape login_accounts

# Verify accounts
twscrape accounts

# Test search
twscrape search "deploy token" --limit 5
```

**Notes:**
- Use dedicated accounts (not personal)
- Accounts should be aged (not brand new)
- May need to solve CAPTCHAs during login
- Sessions stored in `~/.twscrape/accounts.db`
- Disable in config.yaml if not using: `x_detector.enabled: false`

## Running

### Development

```bash
python -m clankandclaw.main
```

### Production

```bash
# With proper environment variables set
python -m clankandclaw.main
```

The service will:
1. Initialize SQLite database
2. Start all async workers
3. Begin polling for signals
4. Send review notifications to Telegram
5. Execute approved deploys

## Testing

Run all tests:
```bash
pytest
```

Run with coverage:
```bash
pytest --cov=clankandclaw --cov-report=html
```

Run specific test file:
```bash
pytest tests/core/test_pipeline.py -v
```

## Project Structure

```
clankandclaw/
├── main.py                    # Entrypoint
├── config.py                  # Configuration loading
├── models/
│   └── token.py              # Pydantic models
├── database/
│   └── manager.py            # SQLite persistence
├── core/
│   ├── supervisor.py         # Worker lifecycle management
│   ├── pipeline.py           # Candidate orchestration
│   ├── filter.py             # Quick filter rules
│   ├── scorer.py             # Scoring heuristics
│   ├── router.py             # Platform routing
│   ├── review_queue.py       # Review locking
│   ├── detectors/
│   │   ├── x_detector.py     # X signal normalization
│   │   ├── farcaster_detector.py # Farcaster signal normalization
│   │   └── gecko_detector.py # Gecko signal normalization
│   └── workers/
│       ├── x_detector_worker.py      # X polling worker
│       ├── farcaster_detector_worker.py  # Farcaster polling worker
│       ├── gecko_detector_worker.py  # Gecko polling worker
│       └── telegram_worker.py        # Telegram bot worker
├── deployers/
│   ├── base.py               # Deployer protocol
│   └── clanker.py            # Clanker implementation
├── utils/
│   ├── extraction.py         # Token name/symbol extraction
│   ├── image_fetcher.py      # Image fetching with SSRF protection
│   ├── ipfs.py               # Pinata IPFS uploads
│   └── llm.py                # LLM fallback interface
└── telegram/
    └── bot.py                # Telegram message formatting
```

## Security

### SSRF Protection

The image fetcher includes comprehensive SSRF protection:
- Blocks private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Blocks localhost and link-local addresses
- Validates resolved IPs before connection
- Protects against DNS rebinding attacks
- Enforces size limits (10MB max)
- Validates content types

### Input Validation

- EVM address validation with regex
- ISO 8601 datetime validation with timezone awareness
- Pydantic strict mode prevents extra fields
- SQL injection protection via parameterized queries
- Foreign key integrity enforced at database level

## Development Status

### Implemented ✅

- Core models and validation
- SQLite persistence with migrations and foreign keys
- Filter, scorer, and router logic
- Detector normalization (X and GeckoTerminal)
- Clanker payload builder with web3 framework
- Image fetcher with SSRF protection
- IPFS upload client (Pinata)
- Telegram bot with aiogram (inline keyboards, callbacks)
- Async worker framework with lifecycle management
- Supervisor with graceful shutdown and signal handling
- X polling with twscrape integration
- Farcaster polling integration (Neynar)
- GeckoTerminal polling with httpx
- Loop throughput optimizations:
  - bounded concurrency per detector
  - non-blocking pipeline execution via `asyncio.to_thread`
  - HTTP client reuse + retry/backoff for transient errors
- SQLite runtime optimizations:
  - WAL + busy timeout
  - hot-path indexes for queue/deploy queries
  - retry on transient `database is locked`
- Deploy preparation pipeline (extraction, image fetch, IPFS upload)
- Context-aware image selection:
  - ranked multi-candidate image selection
  - social avatar/banner de-prioritization
  - placeholder image fallback to avoid wrong-image deploy
- Candidate database queries and reconstruction
- Review queue with locking
- Approval callback handlers
- Deploy result notifications
- Comprehensive test suite (177 tests passing)

### Production Configuration Needed ⚠️

- X account setup for twscrape (30-60 min)
- Production testing and monitoring (30-60 min)
- Operational hardening (systemd, alerts, backups)

### Future Enhancements 🔮

- Bankr deployer implementation
- PostgreSQL migration for better concurrency
- Distributed locking with Redis
- Structured logging with context
- Health check endpoints
- Metrics and monitoring (Prometheus/Grafana)
- Multi-deploy fanout
- Advanced scoring with ML models

## License

Proprietary - All rights reserved

## Support

For issues and questions, contact the development team.
