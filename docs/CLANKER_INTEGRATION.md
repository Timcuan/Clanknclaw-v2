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

Install SDK dependencies in this repository:

```bash
npm install
```

By default, the deployer uses `./node_modules`. You can override via `CLANKER_NODE_MODULES_PATH` if needed.

## Environment Variables

```bash
# Required
DEPLOYER_SIGNER_PRIVATE_KEY=0x...     # Private key for signing transactions
TOKEN_ADMIN_ADDRESS=0x...             # Token admin address
FEE_RECIPIENT_ADDRESS=0x...           # Fee recipient address
ALCHEMY_BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/<KEY>  # preferred
# BASE_RPC_URL=https://mainnet.base.org                            # fallback

# Optional SDK path configuration
# CLANKER_NODE_MODULES_PATH=/path/to/node_modules
# NODE_SCRIPT_PATH=/path/to/scripts/clanker_deploy.mjs  # optional override
```

## Deployment Flow

1. `DeployWorker` calls `ClankerDeployer.deploy(deploy_request)`
2. `preflight()` validates: name (≤50 chars), symbol (≤10 chars, uppercase), EVM addresses, IPFS URI, tax_bps (0–10000), HTTPS RPC
3. `build_clanker_v4_config()` builds the SDK config dict
4. Config is written to a temporary JSON file
5. `node scripts/clanker_deploy.mjs <tmpfile>` is spawned with:
   - `NODE_PATH` → project node_modules (`./node_modules` by default)
   - `DEPLOYER_SIGNER_PRIVATE_KEY` → from env (never logged or written to disk)
   - `BASE_RPC_URL` → from config
6. Script deploys via `Clanker.deploy()` and `waitForTransaction()`
7. JSON result is parsed into `DeployResult`
8. Temp file is deleted in `finally` block

## Rewards Split Policy (Hard Rule)

When rewards are enabled:
- `0.1%` (10 bps) is routed to `TOKEN_ADMIN_ADDRESS` for interface/admin spoof target.
- `99.9%` (9990 bps) is routed to `FEE_RECIPIENT_ADDRESS`.

This split is enforced in deploy payload generation and validated in preflight.

## Claim Fees Integration

System now includes manual claim integration via `clanker-sdk` CLI:
- Telegram command: `/claimfees <token_address>`
- Executes `clanker-sdk rewards claim --token ... --chain base --rpc ... --private-key ... --json`
- Saves claim results to `reward_claim_results` table for auditing

This follows Clanker’s latest rewards/fees direction and keeps claim action explicit (operator-triggered).

## Pool Configuration

The script uses Clanker's standard meme positions (`POOL_POSITIONS.Standard`) with hardcoded pool defaults:
- Paired token: WETH on Base (`0x4200000000000000000000000000000000000006`)
- Starting market cap: `10 ETH`
- Dev buy: disabled (`amount: 0n`)

The pool ticks/positions are computed using `getTickFromMarketCap()` from the SDK.

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
    "recipients": [
      { "recipient": "0xTOKEN_ADMIN...", "admin": "0xTOKEN_ADMIN...", "bps": 10, "token": "Both" },
      { "recipient": "0xFEE_RECIPIENT...", "admin": "0xTOKEN_ADMIN...", "bps": 9990, "token": "Both" }
    ]
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
- NODE_PATH limits module resolution to your configured node_modules path

## config.yaml

```yaml
deployment:
  platform: clanker
  tax_bps: 1000                          # 10% fees in bps
  base_rpc_url: "https://mainnet.base.org"
  # clanker_node_modules_path: "/path/to/node_modules"  # or via CLANKER_NODE_MODULES_PATH env
```

## Troubleshooting

### `script_not_found`
`scripts/clanker_deploy.mjs` not found. Check `NODE_SCRIPT_PATH` or project root.

### `sdk_unavailable`
Node.js not installed or not on PATH. Run `node --version` to verify.

### `sdk_exception: Cannot find module 'clanker-sdk/v4'`
`node_modules` is missing or invalid. Run `npm install` in this project, or set `CLANKER_NODE_MODULES_PATH` correctly.

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
- [ ] `npm install` has been run in this project
- [ ] `DEPLOYER_SIGNER_PRIVATE_KEY` set and wallet has ETH for gas
- [ ] `BASE_RPC_URL` is a working HTTPS endpoint
- [ ] Test deploy on Base Sepolia testnet first
- [ ] Verify token appears on Basescan
- [ ] Verify Telegram notifications work
- [ ] Monitor logs for errors
