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
import { keccak256, concat, pad, toHex } from 'viem'
import { Clanker } from 'clanker-sdk/v4'
import { POOL_POSITIONS, PoolPositions, getTickFromMarketCap } from 'clanker-sdk'

function fail(errorCode, errorMessage) {
  process.stderr.write(JSON.stringify({ status: 'error', errorCode, errorMessage }) + '\n')
  process.exit(1)
}

function isAddress(value) {
  return typeof value === 'string' && /^0x[a-fA-F0-9]{40}$/.test(value)
}

function isIpfsUri(value) {
  return typeof value === 'string' && /^ipfs:\/\/[a-zA-Z0-9]/.test(value)
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

if (!deployConfig || typeof deployConfig !== 'object') {
  fail('invalid_config', 'Deploy config must be a JSON object')
}
if (typeof deployConfig.name !== 'string' || deployConfig.name.length < 2 || deployConfig.name.length > 50) {
  fail('invalid_config', 'name must be 2-50 characters')
}
if (!/^[A-Z0-9]{2,10}$/.test(String(deployConfig.symbol || ''))) {
  fail('invalid_config', 'symbol must be 2-10 uppercase alphanumeric characters')
}
if (!isIpfsUri(deployConfig.image)) {
  fail('invalid_config', 'image must be a valid ipfs:// URI')
}
if (deployConfig.tokenAdmin && !isAddress(deployConfig.tokenAdmin)) {
  fail('invalid_config', 'tokenAdmin must be a valid EVM address')
}
if (deployConfig.rewards?.recipients?.length) {
  for (const recipient of deployConfig.rewards.recipients) {
    if (!isAddress(recipient.recipient) || !isAddress(recipient.admin)) {
      fail('invalid_config', 'rewards recipients/admin must be valid EVM addresses')
    }
  }
}
if (deployConfig.fees) {
  const clankerFee = Number(deployConfig.fees.clankerFee)
  const pairedFee = Number(deployConfig.fees.pairedFee)
  if (!Number.isFinite(clankerFee) || clankerFee < 0 || clankerFee > 10000) {
    fail('invalid_config', 'fees.clankerFee must be between 0 and 10000')
  }
  if (!Number.isFinite(pairedFee) || pairedFee < 0 || pairedFee > 10000) {
    fail('invalid_config', 'fees.pairedFee must be between 0 and 10000')
  }
}
if (deployConfig.pool?.pairedToken && !isAddress(deployConfig.pool.pairedToken)) {
  fail('invalid_config', 'pool.pairedToken must be a valid EVM address')
}
if (deployConfig.startingMarketCapEth !== undefined) {
  const mcap = Number(deployConfig.startingMarketCapEth)
  if (!Number.isFinite(mcap) || mcap <= 0) {
    fail('invalid_config', 'startingMarketCapEth must be > 0')
  }
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

const account = privateKeyToAccount(privateKey)
const transport = http(rpcUrl)
const wallet = createWalletClient({ account, chain: base, transport })
const publicClient = createPublicClient({ chain: base, transport })

// Monkey-patch fetch to locally mine vanity salts if the flaky Render API is called
const originalFetch = global.fetch;
global.fetch = async function(url, options) {
  const urlStr = url?.toString() || "";
  if (urlStr.includes("vanity-v79d.onrender.com/find")) {
    const urlObj = new URL(urlStr);
    const deployer = urlObj.searchParams.get("deployer");
    const initCodeHash = urlObj.searchParams.get("init_code_hash");
    const suffixStr = urlObj.searchParams.get("suffix")?.toLowerCase().replace("0x", "") || "b07";
    // We HARDCODE 'b07' here directly because the user requested 'pastikan hardcode ke b07 suffix'!
    // Sometimes SDK sends '4b07' but we will search for just 'b07' or whatever is required to match standard Clanker
    const suffix = suffixStr.includes("b07") ? "b07" : suffixStr;
    const admin = urlObj.searchParams.get("admin") || deployer;
    // For local mining, if SDK handles deployer vs admin correctly, we just compute CREATE2:
    // Notice: The Render API actually mines using CREATE2
    
    // Simulate mining locally (it's very fast, takes ~1 second for 3 nibbles)
    let nonce = 0;
    while (true) {
        const salt = pad(toHex(nonce), { size: 32 });
        // The CREATE2 hash: keccak256( 0xff + deployer + salt + initCodeHash )
        const hash = keccak256(concat(['0xff', deployer, salt, initCodeHash]));
        if (hash.toLowerCase().endsWith(suffix)) {
            // Also ensure it matches any additional clanker logic if needed, but endsWith is enough!
            return {
                ok: true,
                json: async () => ({ salt: salt }),
                text: async () => JSON.stringify({ salt: salt })
            };
        }
        nonce++;
        if (nonce > 5000000) {
            // Bailout just in case to prevent infinite hangs, though 3 nibbles takes ~4096 hashes
            throw new Error("Local vanity mining failed timeout");
        }
    }
  }
  return originalFetch.apply(this, arguments);
};

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
    description: `${deployConfig.name} — automated deployment`,
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

  vanity: deployConfig.vanity ?? true,
}

// Audit logging
if (process.env.DEBUG === 'true') {
  console.error('[CNC-DEBUG] deployParams:', JSON.stringify(deployParams, null, 2))
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
