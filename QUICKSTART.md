# Quick Start Guide

Get Clank&Claw MVP running in 10 minutes.

## Prerequisites

- Python 3.11+
- Telegram account
- Pinata account (for IPFS)
- Base wallet with ETH for gas

## Step 1: Install Dependencies

```bash
# Clone repository
git clone <repository-url>
cd clankandclaw

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Step 2: Configure Environment

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your values
nano .env
```

**Required values:**
```bash
DEPLOYER_SIGNER_PRIVATE_KEY=0x...  # Your wallet private key
TOKEN_ADMIN_ADDRESS=0x...          # Your wallet address
FEE_RECIPIENT_ADDRESS=0x...        # Your wallet address
TELEGRAM_BOT_TOKEN=...             # From @BotFather
TELEGRAM_CHAT_ID=...               # Your chat ID (use @userinfobot)
PINATA_JWT=...                     # From Pinata dashboard
BASE_RPC_URL=https://mainnet.base.org
```

Install Node.js dependencies once:

```bash
npm install
```

## Step 3: Configure Detectors (Optional)

Edit `config.yaml` to enable/disable detectors:

```yaml
x_detector:
  enabled: false  # Set to true if you have X accounts configured

gecko_detector:
  enabled: true
  poll_interval: 25.0
  networks: ["base", "eth", "solana", "bsc"]
  max_requests_per_minute: 40
```

## Step 4: Setup X Polling (Optional)

If you want X polling, configure twscrape:

```bash
# Add X account
twscrape add_account myaccount username password email email_password

# Login
twscrape login_accounts

# Test
twscrape search "deploy token" --limit 5
```

## Step 5: Run the Service

```bash
# Activate virtual environment
source venv/bin/activate

# Run
python -m clankandclaw.main
```

You should see:
```
INFO:clankandclaw.database.manager:Database initialized
INFO:clankandclaw.core.supervisor:Starting supervisor
INFO:clankandclaw.core.workers.x_detector_worker:X detector worker started
INFO:clankandclaw.core.workers.gecko_detector_worker:Gecko detector worker started
INFO:clankandclaw.core.workers.telegram_worker:Telegram worker started
INFO:clankandclaw.core.supervisor:Supervisor started with workers: ['x_detector', 'gecko_detector', 'telegram', 'deploy']
```

## Step 6: Test Telegram Bot

1. Open Telegram
2. Find your bot (search for the bot username)
3. Send `/start`
4. You should get a welcome message

## Step 7: Monitor Activity

Watch the logs for activity:
```bash
# In the terminal where the service is running, you'll see:
INFO:clankandclaw.core.workers.gecko_detector_worker:Fetched 20 new pools from GeckoTerminal network=base
INFO:clankandclaw.core.workers.gecko_detector_worker:Candidate gecko-base:0x... scored 82 -> priority_review
INFO:clankandclaw.telegram.bot:Sent review notification for gecko-base:0x...
```

Check your Telegram for review notifications!

## Step 8: Approve a Deploy

When you receive a review notification in Telegram:
1. Review the candidate details
2. Click "✅ Approve" or "❌ Reject"
3. If approved, the system will:
   - Extract token name/symbol
   - Fetch and upload image to IPFS
   - Create and upload metadata to IPFS
   - Execute Clanker deploy
   - Send result notification

## Troubleshooting

### Service won't start

```bash
# Check Python version
python --version  # Should be 3.11+

# Check dependencies
pip list | grep -E "aiogram|httpx|web3|twscrape"

# Check environment variables
cat .env | grep -v "^#"
```

### Telegram bot not responding

```bash
# Test bot token
curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe

# Test chat ID
curl https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

### No signals detected

```bash
# Check GeckoTerminal API
curl "https://api.geckoterminal.com/api/v2/networks/base/new_pools?page=1"

# Check X polling (if enabled)
twscrape accounts
twscrape search "deploy token" --limit 5
```

### Deploy fails

```bash
# Check wallet balance
# You need ETH on Base for gas fees

# Check RPC connection
python -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org')); print(w3.is_connected())"

# Check signer env
cat .env | grep DEPLOYER_SIGNER_PRIVATE_KEY
```

## Next Steps

- Read [DEPLOYMENT.md](DEPLOYMENT.md) for production deployment
- Read [README.md](README.md) for architecture details
- Check [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for feature status
- Review [CHANGELOG.md](CHANGELOG.md) for version history

## Getting Help

If you encounter issues:
1. Check the logs for error messages
2. Review the troubleshooting section above
3. Check [DEPLOYMENT.md](DEPLOYMENT.md) for detailed troubleshooting
4. Contact the development team with logs and error details

## Production Checklist

Before deploying to production:

- [ ] Configure X accounts with twscrape (if using X polling)
- [ ] Run `npm install` in this repo (for clanker-sdk + viem)
- [ ] Test on testnet first
- [ ] Set up monitoring and alerting
- [ ] Configure backup strategy
- [ ] Review security settings
- [ ] Test full end-to-end flow
- [ ] Document runbook for operators

## Quick Commands Reference

```bash
# Start service
python -m clankandclaw.main

# Run tests
pytest

# Check database
sqlite3 clankandclaw.db "SELECT * FROM signal_candidates LIMIT 5;"

# View recent decisions
sqlite3 clankandclaw.db "SELECT * FROM candidate_decisions ORDER BY candidate_id DESC LIMIT 10;"

# Check review queue
sqlite3 clankandclaw.db "SELECT * FROM review_items WHERE status='pending';"

# Test Telegram bot
curl https://api.telegram.org/bot<TOKEN>/getMe

# Test X search (if configured)
twscrape search "deploy token" --limit 5

# Test GeckoTerminal API
curl "https://api.geckoterminal.com/api/v2/networks/base/new_pools?page=1"
```

Happy deploying! 🚀
