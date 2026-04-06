#!/bin/bash
# ------------------------------------------------------------------------------
# Clank&Claw Mission Control Sync Script
# ------------------------------------------------------------------------------
# Automates the update and restart sequence on the VPS.
# Run this from the project root on your server.
# ------------------------------------------------------------------------------

set -e

APP_DIR="/opt/clankandclaw/app"
SERVICE_NAME="clanknclaw-v2.service"

# 1. Colors for visibility
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${CYAN}🛰 Clank&Claw Mission Control Sync Starting...${NC}"

# 2. Check directory
if [ ! -d "$APP_DIR" ]; then
    echo -e "${YELLOW}Warning: Default /opt path not found. Using current directory.${NC}"
    APP_DIR=$(pwd)
fi

cd "$APP_DIR"

# 3. Code Update
echo -e "${CYAN}📥 Pulling latest Mission Control fixes...${NC}"
git pull origin main

# 3.1 .env Sanitization (Deduplicate keys)
if [ -f ".env" ]; then
    echo -e "${CYAN}🧹 Sanitizing .env variables...${NC}"
    # Keep only the last occurrence of each key to ensure updates override old values
    awk -F= '!a[$1]++{b[c++]=$1} {v[$1]=$0} END{for(i=0;i<c;i++) print v[b[i]]}' .env > .env.tmp && mv .env.tmp .env
fi

# 4. Dependency Update (Safe skip if no change)
if [ -f "requirements.txt" ]; then
    echo -e "${CYAN}📦 Syncing dependencies...${NC}"
    # Use venv or .venv if present
    if [ -d "venv" ]; then
        ./venv/bin/pip install -r requirements.txt --quiet
    elif [ -d ".venv" ]; then
        ./.venv/bin/pip install -r requirements.txt --quiet
    else
        pip install -r requirements.txt --quiet || echo "Warning: pip install failed. Check if venv is missing."
    fi
fi

# 5. Service Refresh
echo -e "${CYAN}🔄 Restarting Clank&Claw Service...${NC}"
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"

# 6. Verification
echo -e "${GREEN}✅ Mission Control v2.1 Sync Complete!${NC}"
echo -e "System state: $(sudo systemctl is-active $SERVICE_NAME)"
echo -e "Use ${YELLOW}journalctl -u $SERVICE_NAME -f${NC} to monitor logs."
echo -e "Run ${YELLOW}/status${NC} in Telegram to verify the new UI."
