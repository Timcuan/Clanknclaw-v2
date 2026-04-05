# Clank&Claw MVP

Automated token deployment system that detects promising Base token deploy signals, routes them through a deterministic scoring pipeline, requests Telegram approval, and executes Clanker deploys.

## Architecture

Single async Python service with:
- **X Detector**: Polls X/Twitter for deploy signals
- **GMGN Detector**: Polls GMGN for new token launches
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

gmgn_detector:
  enabled: true                # Enable GMGN polling
  poll_interval: 60.0          # Polling interval in seconds
  api_url: "https://gmgn.ai/defi/quotation/v1/tokens/base/new"
  max_results: 20              # Max results per poll

deployment:
  platform: clanker            # Deploy platform (only clanker in MVP)
  tax_bps: 1000               # Tax basis points (1000 = 10%)
```

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
- `CLANKER_CONTRACT_ADDRESS`: Clanker contract address

**Optional:**
- X/Twitter accounts configured via twscrape (see below)

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
│   │   └── gmgn_detector.py  # GMGN signal normalization
│   └── workers/
│       ├── x_detector_worker.py      # X polling worker
│       ├── gmgn_detector_worker.py   # GMGN polling worker
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
- Detector normalization (X and GMGN)
- Clanker payload builder with web3 framework
- Image fetcher with SSRF protection
- IPFS upload client (Pinata)
- Telegram bot with aiogram (inline keyboards, callbacks)
- Async worker framework with lifecycle management
- Supervisor with graceful shutdown and signal handling
- X polling with twscrape integration
- GMGN polling with httpx
- Deploy preparation pipeline (extraction, image fetch, IPFS upload)
- Candidate database queries and reconstruction
- Review queue with locking
- Approval callback handlers
- Deploy result notifications
- Comprehensive test suite (67 tests passing)

### Production Configuration Needed ⚠️

- X account setup for twscrape (30-60 min)
- Clanker contract ABI integration (60-90 min)
- Production testing and monitoring (30-60 min)

**Estimated time to production: 2-4 hours**

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
