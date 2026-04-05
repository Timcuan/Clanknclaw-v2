# Deployment Guide

## Prerequisites

### System Requirements

- Ubuntu 20.04+ or similar Linux distribution
- Python 3.11+
- systemd (for service management)
- 1GB RAM minimum
- 10GB disk space

### External Services

1. **Telegram Bot**
   - Create bot via @BotFather
   - Get bot token
   - Get chat ID (use @userinfobot)

2. **Pinata Account**
   - Sign up at https://pinata.cloud
   - Generate JWT token from API Keys section

3. **Base RPC**
   - Use public endpoint: https://mainnet.base.org
   - Or get dedicated endpoint from Alchemy/Infura

4. **Deployer Wallet**
   - Create new wallet or use existing
   - Fund with ETH for gas fees
   - Export private key (keep secure!)

## Installation on Hetzner

### 1. Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3-pip

# Install system dependencies
sudo apt install -y git sqlite3
```

### 2. Application Setup

```bash
# Create application user
sudo useradd -r -s /bin/bash -d /opt/clankandclaw clankandclaw

# Create application directory
sudo mkdir -p /opt/clankandclaw
sudo chown clankandclaw:clankandclaw /opt/clankandclaw

# Switch to application user
sudo -u clankandclaw -i

# Clone repository
cd /opt/clankandclaw
git clone <repository-url> app
cd app

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit environment variables
nano .env
```

Set the following in `.env`:

```bash
DEPLOYER_SIGNER_PRIVATE_KEY=0x...
TOKEN_ADMIN_ADDRESS=0x...
FEE_RECIPIENT_ADDRESS=0x...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
PINATA_JWT=...
BASE_RPC_URL=https://mainnet.base.org
CLANKER_CONTRACT_ADDRESS=0x...
```

Review and adjust `config.yaml`:

```bash
nano config.yaml
```

### 4. X/Twitter Polling Setup (Optional)

If you want to enable X polling, you need to configure twscrape with authenticated accounts:

```bash
# Activate virtual environment
source venv/bin/activate

# Add X accounts to twscrape
# You'll need username, password, email, and email password for each account
twscrape add_accounts accounts.txt username:password:email:email_password

# Or add accounts interactively
twscrape add_account account1 username password email email_password

# Login to accounts (this will save session cookies)
twscrape login_accounts

# Verify accounts are working
twscrape accounts

# Test search functionality
twscrape search "deploy token" --limit 5
```

**Important Notes:**
- Use dedicated X accounts (not your personal account)
- Accounts need to be aged (not brand new)
- You may need to solve CAPTCHAs during login
- Sessions are stored in `~/.twscrape/accounts.db`
- Rotate accounts if rate limited

**Disable X Polling:**
If you don't want X polling, set in `config.yaml`:
```yaml
x_detector:
  enabled: false
```

### 5. Clanker Contract Configuration

Get the Clanker contract address and ABI:

```bash
# Set contract address in .env
CLANKER_CONTRACT_ADDRESS=0x...  # Get from Clanker team

# For production deployment, you'll need to:
# 1. Get the contract ABI from Clanker
# 2. Add ABI loading in clankandclaw/deployers/clanker.py
# 3. Implement actual contract call (currently uses mock)
```

**Current Status:**
- Web3 framework is ready
- Transaction building is implemented
- Contract ABI needs to be loaded
- See `clankandclaw/deployers/clanker.py` for implementation

### 6. Database Initialization

```bash
# Initialize database (will be created on first run)
python -m clankandclaw.main
# Press Ctrl+C after seeing "Supervisor started"
```

### 7. Systemd Service

Create service file:

```bash
sudo nano /etc/systemd/system/clankandclaw.service
```

Add the following:

```ini
[Unit]
Description=Clank&Claw MVP Token Deployment Service
After=network.target

[Service]
Type=simple
User=clankandclaw
Group=clankandclaw
WorkingDirectory=/opt/clankandclaw/app
Environment="PATH=/opt/clankandclaw/app/venv/bin"
EnvironmentFile=/opt/clankandclaw/app/.env
ExecStart=/opt/clankandclaw/app/venv/bin/python -m clankandclaw.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=clankandclaw

[Install]
WantedBy=multi-user.target
```

Enable and start service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable clankandclaw
sudo systemctl start clankandclaw
```

### 8. Verify Deployment

```bash
# Check service status
sudo systemctl status clankandclaw

# View logs
sudo journalctl -u clankandclaw -f

# Check database
sqlite3 /opt/clankandclaw/app/clankandclaw.db "SELECT name FROM sqlite_master WHERE type='table';"

# Verify workers are running
sudo journalctl -u clankandclaw | grep "worker started"

# Test X polling (if enabled)
sudo journalctl -u clankandclaw | grep "X detector"

# Test GMGN polling
sudo journalctl -u clankandclaw | grep "GMGN detector"

# Test Telegram bot
# Send /start to your bot and check for response
```

## Monitoring

### Service Management

```bash
# Start service
sudo systemctl start clankandclaw

# Stop service
sudo systemctl stop clankandclaw

# Restart service
sudo systemctl restart clankandclaw

# View status
sudo systemctl status clankandclaw

# View logs (last 100 lines)
sudo journalctl -u clankandclaw -n 100

# Follow logs in real-time
sudo journalctl -u clankandclaw -f

# View logs for specific date
sudo journalctl -u clankandclaw --since "2026-04-05"
```

### Database Queries

```bash
# Connect to database
sqlite3 /opt/clankandclaw/app/clankandclaw.db

# View recent candidates
SELECT id, source, decision, score FROM signal_candidates 
JOIN candidate_decisions ON signal_candidates.id = candidate_decisions.candidate_id 
ORDER BY id DESC LIMIT 10;

# View pending reviews
SELECT * FROM review_items WHERE status = 'pending';

# View deploy history (when implemented)
SELECT * FROM deploy_results ORDER BY completed_at DESC LIMIT 10;
```

## Backup

### Database Backup

```bash
# Create backup script
sudo nano /opt/clankandclaw/backup.sh
```

Add:

```bash
#!/bin/bash
BACKUP_DIR="/opt/clankandclaw/backups"
DB_PATH="/opt/clankandclaw/app/clankandclaw.db"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR
sqlite3 $DB_PATH ".backup '$BACKUP_DIR/clankandclaw_$DATE.db'"

# Keep only last 7 days
find $BACKUP_DIR -name "clankandclaw_*.db" -mtime +7 -delete
```

Make executable and add to cron:

```bash
sudo chmod +x /opt/clankandclaw/backup.sh
sudo crontab -e
```

Add:

```
0 */6 * * * /opt/clankandclaw/backup.sh
```

## Updates

### Application Update

```bash
# Stop service
sudo systemctl stop clankandclaw

# Switch to application user
sudo -u clankandclaw -i
cd /opt/clankandclaw/app

# Backup database
cp clankandclaw.db clankandclaw.db.backup

# Pull updates
git pull

# Update dependencies
source venv/bin/activate
pip install -r requirements.txt

# Run migrations (if any)
python -m clankandclaw.main
# Press Ctrl+C after seeing "Database initialized"

# Exit application user
exit

# Start service
sudo systemctl start clankandclaw

# Verify
sudo systemctl status clankandclaw
sudo journalctl -u clankandclaw -f
```

## Troubleshooting

### Service Won't Start

```bash
# Check service status
sudo systemctl status clankandclaw

# Check logs
sudo journalctl -u clankandclaw -n 50

# Common issues:
# 1. Missing environment variables
cat /opt/clankandclaw/app/.env

# 2. Database permissions
ls -la /opt/clankandclaw/app/clankandclaw.db

# 3. Python dependencies
sudo -u clankandclaw -i
cd /opt/clankandclaw/app
source venv/bin/activate
pip list
```

### Database Locked

```bash
# Check for stale connections
sudo lsof /opt/clankandclaw/app/clankandclaw.db

# If needed, restart service
sudo systemctl restart clankandclaw
```

### High Memory Usage

```bash
# Check memory usage
ps aux | grep clankandclaw

# Check system memory
free -h

# If needed, restart service
sudo systemctl restart clankandclaw
```

### Telegram Not Responding

```bash
# Verify bot token
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getMe

# Verify chat ID
# Send message to bot and check updates
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates

# Check logs for Telegram errors
sudo journalctl -u clankandclaw | grep -i telegram
```

### X Polling Issues

```bash
# Check if twscrape is installed
sudo -u clankandclaw -i
cd /opt/clankandclaw/app
source venv/bin/activate
pip show twscrape

# Check twscrape accounts
twscrape accounts

# Test search manually
twscrape search "deploy token" --limit 5

# Check logs for X detector errors
sudo journalctl -u clankandclaw | grep "X detector"

# If accounts are rate limited, add more accounts
twscrape add_account account2 username password email email_password
twscrape login_accounts

# Disable X polling if needed
# Edit config.yaml and set x_detector.enabled: false
```

### GMGN Polling Issues

```bash
# Test GMGN API manually
curl "https://gmgn.ai/defi/quotation/v1/tokens/base/new?limit=5"

# Check logs for GMGN detector errors
sudo journalctl -u clankandclaw | grep "GMGN detector"

# Verify API URL in config.yaml
cat config.yaml | grep -A 5 "gmgn_detector"

# Disable GMGN polling if needed
# Edit config.yaml and set gmgn_detector.enabled: false
```

### Web3 Deployment Issues

```bash
# Check if web3.py is installed
sudo -u clankandclaw -i
cd /opt/clankandclaw/app
source venv/bin/activate
pip show web3

# Test RPC connection
python -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org')); print(w3.is_connected())"

# Check deployer wallet balance
python -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org')); print(w3.eth.get_balance('YOUR_DEPLOYER_ADDRESS'))"

# Check logs for deployment errors
sudo journalctl -u clankandclaw | grep -i "deploy"

# Verify contract address is set
cat .env | grep CLANKER_CONTRACT_ADDRESS
```

## Security

### File Permissions

```bash
# Ensure proper ownership
sudo chown -R clankandclaw:clankandclaw /opt/clankandclaw

# Protect sensitive files
chmod 600 /opt/clankandclaw/app/.env
chmod 600 /opt/clankandclaw/app/clankandclaw.db
```

### Firewall

```bash
# Only allow SSH (no inbound ports needed for this service)
sudo ufw allow 22/tcp
sudo ufw enable
```

### Private Key Security

- Never commit `.env` to git
- Use environment variables or secrets manager
- Rotate keys periodically
- Use separate wallets for different roles
- Monitor wallet balances

## Performance Tuning

### Polling Intervals

Edit `clankandclaw/core/workers/*.py`:

```python
# X detector (default: 30s)
XDetectorWorker(db, poll_interval=30.0)

# GMGN detector (default: 60s)
GMGNDetectorWorker(db, poll_interval=60.0)
```

### Database Optimization

```bash
# Vacuum database periodically
sqlite3 /opt/clankandclaw/app/clankandclaw.db "VACUUM;"

# Analyze for query optimization
sqlite3 /opt/clankandclaw/app/clankandclaw.db "ANALYZE;"
```

## Scaling

For higher throughput:

1. **Separate GMGN Collector**
   - Run GMGN collector on separate host with residential proxy
   - Forward normalized events to main executor via HTTP/queue

2. **Database Migration**
   - Migrate from SQLite to PostgreSQL for better concurrency
   - Update `database/manager.py` to use PostgreSQL

3. **Multiple Deployers**
   - Add Bankr deployer implementation
   - Implement multi-deploy fanout

4. **Load Balancing**
   - Run multiple instances with shared PostgreSQL
   - Use Redis for distributed locking

## Support

For deployment issues:
- Check logs: `sudo journalctl -u clankandclaw -f`
- Review configuration: `.env` and `config.yaml`
- Verify external services: Telegram, Pinata, RPC
- Contact development team with logs and error messages
