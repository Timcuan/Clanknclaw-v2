# Clanker v4.0.0 SDK Integration Guide

This document describes how Clank&Claw integrates with the Clanker v4.0.0 SDK for token deployment on Base.

## Overview

Clanker is a token deployment protocol on Base that creates ERC20 tokens with Uniswap V3 liquidity pools. Clank&Claw uses the **Clanker v4.0.0 TypeScript SDK** via a Node.js subprocess wrapper, allowing the Python application to call the SDK without rewriting its logic in Python.

## Architecture

```
ClankerDeployer (Python)
    └── subprocess: node scripts/clanker_deploy.mjs <config.json>
            └── Clanker SDK v4 (TypeScript/viem)
                    └── Base RPC → Base blockchain
```

The Node.js script reads deployment config from a temp JSON file, deploys via the SDK, and outputs a JSON result to stdout.

## Prerequisites

### 1. Node.js

Install Node.js v18+ from https://nodejs.org

```bash
node --version  # should print v18.x or higher
```

### 2. Clanker SDK node_modules

The SDK is already installed in the sibling `Clank n Claw - Executor` project. Point `EXECUTOR_PATH` to it:

```bash
EXECUTOR_PATH="/path/to/Clank n Claw - Executor"
```

The Python deployer sets `NODE_PATH=$EXECUTOR_PATH/node_modules` when running the script, so no separate `npm install` is needed.

## Environment Variables

```bash
# Required
DEPLOYER_SIGNER_PRIVATE_KEY=0x...     # Private key for signing transactions
TOKEN_ADMIN_ADDRESS=0x...             # Token admin address
FEE_RECIPIENT_ADDRESS=0x...           # Fee recipient address
BASE_RPC_URL=https://mainnet.base.org # Base RPC endpoint (must be HTTPS)

# SDK path configuration
EXECUTOR_PATH=/path/to/Clank n Claw - Executor
# NODE_SCRIPT_PATH=/path/to/scripts/clanker_deploy.mjs  # optional override
```

## Deployment Flow

1. `DeployWorker` calls `ClankerDeployer.deploy(deploy_request)`
2. `preflight()` validates: name (≤50 chars), symbol (≤10 chars, uppercase), EVM addresses, IPFS URI, tax_bps (0–10000), HTTPS RPC
3. `build_clanker_v4_config()` builds the SDK config dict
4. Config is written to a temporary JSON file
5. `node scripts/clanker_deploy.mjs <tmpfile>` is spawned with:
   - `NODE_PATH` → Executor's node_modules
   - `DEPLOYER_SIGNER_PRIVATE_KEY` → from env (never logged or written to disk)
   - `BASE_RPC_URL` → from config
6. Script deploys via `Clanker.deploy()` and `waitForTransaction()`
7. JSON result is parsed into `DeployResult`
8. Temp file is deleted in `finally` block

## Pool Configuration

The script uses Clanker's standard meme positions (`POOL_POSITIONS.Standard`) with a configurable starting market cap (default: 10 ETH). The pool is computed using `getTickFromMarketCap()` from the SDK.

## SDK Config Structure

```json
{
  "name": "Token Name",
  "symbol": "SYMBOL",
  "image": "ipfs://...",
  "tokenAdmin": "0x...",
  "description": "...",
  "context": { "interface": "Clank&Claw", "platform": "automated" },
  "fees": { "type": "static", "clankerFee": 1000, "pairedFee": 1000 },
  "rewards": {
    "recipients": [{ "recipient": "0x...", "admin": "0x...", "bps": 10000, "token": "Both" }]
  },
  "feeRecipient": "0x...",
  "taxBps": 1000,
  "vault": null,
  "devBuy": null
}
```

`tokenAdmin` and `rewards` are omitted when `token_admin_enabled=False` / `token_reward_enabled=False`.

## Output Parsing

On success, the script writes to stdout:
```json
{ "status": "success", "txHash": "0x...", "contractAddress": "0x..." }
```

On failure, the script writes to stderr:
```json
{ "status": "error", "errorCode": "sdk_exception", "errorMessage": "..." }
```

`parse_sdk_output()` in `clanker.py` handles both cases and never raises exceptions.

## Security

- Private key is passed only via environment variable, never written to disk or logged
- All EVM addresses validated with regex before use
- IPFS URIs validated for `ipfs://` prefix
- RPC URL enforced to use HTTPS
- Script path resolved to absolute path before use
- NODE_PATH limits module resolution to the Executor's known-good node_modules

## config.yaml

```yaml
deployment:
  platform: clanker
  tax_bps: 1000                          # 10% fees in bps
  base_rpc_url: "https://mainnet.base.org"
  # executor_path: "/path/to/Clank n Claw - Executor"  # or via EXECUTOR_PATH env
```

## Troubleshooting

### `script_not_found`
`scripts/clanker_deploy.mjs` not found. Check `NODE_SCRIPT_PATH` or project root.

### `sdk_unavailable`
Node.js not installed or not on PATH. Run `node --version` to verify.

### `sdk_exception: Cannot find module 'clanker-sdk/v4'`
`EXECUTOR_PATH` is wrong or node_modules not installed in Executor project. Run `bun install` in the Executor directory.

### `invalid_rpc_url`
`BASE_RPC_URL` must start with `https://`. Public default: `https://mainnet.base.org`

### `timeout`
Deployment timed out after 120 seconds. Check RPC endpoint availability and gas price.

### Transaction reverts
- Insufficient ETH for gas — fund the signer wallet
- Invalid address — check TOKEN_ADMIN_ADDRESS and FEE_RECIPIENT_ADDRESS
- Network congestion — retry with higher gas

## Testing Checklist

Before production deployment:

- [ ] `node --version` returns v18+
- [ ] `EXECUTOR_PATH` points to correct directory with `node_modules/clanker-sdk`
- [ ] `DEPLOYER_SIGNER_PRIVATE_KEY` set and wallet has ETH for gas
- [ ] `BASE_RPC_URL` is a working HTTPS endpoint
- [ ] Test deploy on Base Sepolia testnet first
- [ ] Verify token appears on Basescan
- [ ] Verify Telegram notifications work
- [ ] Monitor logs for errors
