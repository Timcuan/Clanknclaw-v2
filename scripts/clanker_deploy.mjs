/**
 * Clanker SDK v4 deploy wrapper script.
 *
 * Called by Python ClankerDeployer via subprocess:
 *   node scripts/clanker_deploy.mjs <config_file_path>
 *
 * Environment variables required:
 *   DEPLOYER_SIGNER_PRIVATE_KEY  - 0x-prefixed private key
 *   BASE_RPC_URL                 - HTTPS RPC endpoint (default: https://mainnet.base.org)
 *
 * Exits 0 on success with JSON to stdout:
 *   { "status": "success", "txHash": "0x...", "contractAddress": "0x..." }
 *
 * Exits 1 on failure with JSON to stderr:
 *   { "status": "error", "errorCode": "...", "errorMessage": "..." }
 */

import { readFileSync } from 'node:fs'
import { createPublicClient, createWalletClient, http } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'
import { base } from 'viem/chains'
import { Clanker } from 'clanker-sdk/v4'
import { POOL_POSITIONS, PoolPositions, getTickFromMarketCap } from 'clanker-sdk'

function fail(errorCode, errorMessage) {
  process.stderr.write(JSON.stringify({ status: 'error', errorCode, errorMessage }) + '\n')
  process.exit(1)
}

// Read config file path from CLI arg
const configPath = process.argv[2]
if (!configPath) {
  fail('missing_config_path', 'Config file path required as first argument')
}

let deployConfig
try {
  deployConfig = JSON.parse(readFileSync(configPath, 'utf-8'))
} catch (err) {
  fail('config_read_error', `Failed to read config file: ${String(err)}`)
}

// Validate required env vars
const privateKey = process.env.DEPLOYER_SIGNER_PRIVATE_KEY
if (!privateKey) {
  fail('missing_private_key', 'DEPLOYER_SIGNER_PRIVATE_KEY environment variable is required')
}

const rpcUrl = process.env.BASE_RPC_URL || 'https://mainnet.base.org'
if (!rpcUrl.startsWith('https://')) {
  fail('invalid_rpc_url', 'BASE_RPC_URL must use HTTPS protocol')
}

// Build viem clients
const account = privateKeyToAccount(privateKey)
const transport = http(rpcUrl)
const wallet = createWalletClient({ account, chain: base, transport })
const publicClient = createPublicClient({ chain: base, transport })

// Compute pool config using standard positions
function buildPool(startingMarketCapEth, pairedToken) {
  const standardPositions = POOL_POSITIONS[PoolPositions.Standard]
  const STANDARD_TICK_UPPER = standardPositions[0]?.tickUpper ?? 0
  const TICK_SPACING = 200

  const mcap = (startingMarketCapEth && startingMarketCapEth > 0)
    ? Math.max(0.1, startingMarketCapEth)
    : 10  // default 10 ETH market cap

  const { tickIfToken0IsClanker: rawTick } = getTickFromMarketCap(mcap)
  const alignedTick = Math.round(rawTick / TICK_SPACING) * TICK_SPACING

  return {
    pairedToken: pairedToken || 'WETH',
    tickIfToken0IsClanker: alignedTick,
    tickSpacing: TICK_SPACING,
    positions: [{ tickLower: alignedTick, tickUpper: STANDARD_TICK_UPPER, positionBps: 10000 }],
  }
}

// Build full deploy params
const deployParams = {
  name: deployConfig.name,
  symbol: deployConfig.symbol,
  image: deployConfig.image,
  tokenAdmin: deployConfig.tokenAdmin,
  chainId: base.id,

  metadata: deployConfig.metadata || {
    description: `${deployConfig.name} — deployed via Clank&Claw`,
  },

  context: deployConfig.context || {
    interface: 'Clank&Claw',
    platform: 'automated',
  },

  pool: buildPool(deployConfig.startingMarketCapEth, deployConfig.pool?.pairedToken),

  fees: deployConfig.fees || {
    type: 'static',
    clankerFee: deployConfig.taxBps ?? 1000,
    pairedFee: deployConfig.taxBps ?? 1000,
  },

  rewards: deployConfig.rewards || {
    recipients: [{
      admin: deployConfig.tokenAdmin,
      recipient: deployConfig.feeRecipient,
      bps: 10000,
      token: 'Both',
    }],
  },

  vanity: false,
  amount: 0n,
}

// Execute deployment
try {
  const clanker = new Clanker({ wallet, publicClient })
  const result = await clanker.deploy(deployParams)

  if (result.error) {
    fail('deploy_error', `Clanker deploy failed: ${String(result.error)}`)
  }

  const { address } = await result.waitForTransaction()

  process.stdout.write(JSON.stringify({
    status: 'success',
    txHash: result.txHash,
    contractAddress: address,
  }) + '\n')
  process.exit(0)
} catch (err) {
  fail('sdk_exception', String(err))
}
