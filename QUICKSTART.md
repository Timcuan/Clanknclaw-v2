# Quick Start

This guide gets Clank&Claw running quickly for local validation before production deployment.

## 1. Prerequisites

- Python `3.11+`
- Node.js + npm
- Telegram bot token + chat ID
- Pinata JWT
- Base wallet funded for gas

## 2. Install

```bash
git clone <repository-url>
cd clankandclaw

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
npm install
```

## 3. Configure

```bash
cp .env.example .env
nano .env
```

Required `.env` values:

```bash
DEPLOYER_SIGNER_PRIVATE_KEY=0x...
TOKEN_ADMIN_ADDRESS=0x...
FEE_RECIPIENT_ADDRESS=0x...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
PINATA_JWT=...
BASE_RPC_URL=https://mainnet.base.org
```

Optional anti-block transport overrides:

```bash
STEALTH_ENABLED=true
STEALTH_ROTATE_EVERY=50
STEALTH_JITTER_SIGMA_PCT=0.15
STEALTH_JITTER_MIN_MS=200
STEALTH_JITTER_MAX_MS=3000
```

## 4. Optional source toggles

Edit `config.yaml` if you want to disable detectors during bring-up:

```yaml
x_detector:
  enabled: false

farcaster_detector:
  enabled: true

gecko_detector:
  enabled: true
```

If you enable X polling, configure `twscrape` accounts first.

## 5. Run service

```bash
source venv/bin/activate
python -m clankandclaw.main
```

Expected startup signals in logs:

- `Database initialized`
- `Starting supervisor`
- detector workers started (`x_detector`, `farcaster_detector`, `gecko_detector`)
- `telegram` and `deploy` workers started

## 6. Verify bot path

1. Open Telegram.
2. Add bot to target chat/supergroup.
3. Send `/pair` in the target chat.
4. If using forum supergroup:
   - ensure bot is admin and has `Manage Topics`
   - run `/autothread` (or re-run `/pair`) to auto-create/bind topics
5. Send `/start` to verify command response.
6. Wait for review notifications from incoming signals.
7. Approve one candidate to trigger deploy flow.

## 7. Fast health checks

```bash
# tests
pytest -q

# telegram auth
curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe

# gecko source reachability
curl "https://api.geckoterminal.com/api/v2/networks/base/new_pools?page=1"

# idempotency/dedup guardrails in logs
sudo journalctl -u clankandclaw | grep -i "already has a successful deployment"
sudo journalctl -u clankandclaw | grep -i "token_dedup"
```

## 8. Common issues

### Service does not start

```bash
python --version
pip list | grep -E "aiogram|httpx|web3|twscrape"
cat .env | grep -v "^#"
```

### Telegram does not respond

```bash
curl https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

Checklist:

- bot already paired to your current chat (`/pair`)
- `TELEGRAM_BOT_TOKEN` valid
- service running and polling active
- if forum mode is used: bot has admin permission `Manage Topics`
- run `/autothread` after changing permissions

### Deploy path fails

```bash
python -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org')); print(w3.is_connected())"
cat .env | grep DEPLOYER_SIGNER_PRIVATE_KEY
```

## Next documents

- Production runbook: [DEPLOYMENT.md](DEPLOYMENT.md)
- System overview: [README.md](README.md)
- Clanker bridge details: [docs/CLANKER_INTEGRATION.md](docs/CLANKER_INTEGRATION.md)
- Release history: [CHANGELOG.md](CHANGELOG.md)
