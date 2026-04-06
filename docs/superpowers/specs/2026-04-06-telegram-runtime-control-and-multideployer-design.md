# Telegram Runtime Control and Multi-Deployer Design (2026-04-06)

## Objective

Deliver a zero-manual operator control plane in Telegram that can switch runtime behavior instantly:
- Review mode vs auto mode
- Bot notification on/off
- Deployer mode: `clanker` / `bankr` / `both`

The system must remain safe for 24/7 operation, support thread/topic smart binding, and stay backward compatible with current Clanker-first flow.

## Scope

In scope:
- Runtime settings model persisted in SQLite
- Telegram control panel + slash controls
- Runtime resolution in detector/review/deploy flow
- Multi-deployer orchestration contract for `both` mode
- Notification and thread routing adaptation

Out of scope (this phase):
- Bankr contract integration internals
- Advanced strategy rules per chain/source
- External web dashboard

## Requirements

1. Operator can control runtime behavior without editing config files.
2. `AUTO` mode only auto-deploys `priority_review` candidates.
3. Deployer mode is selectable at runtime: `clanker`, `bankr`, `both`.
4. Thread routing is automatic by default and persisted.
5. System remains stable under callback spam/retries/restarts.

## Runtime Settings Model

Use `runtime_settings` as the runtime control store.

Required keys:
- `ops.mode`: `review | auto`
- `ops.bot_enabled`: `on | off`
- `ops.deployer_mode`: `clanker | bankr | both`
- `ops.auto_rule`: `priority_review_only` (fixed in this phase)

Thread binding keys:
- `telegram.thread.review`
- `telegram.thread.deploy`
- `telegram.thread.claim`
- `telegram.thread.ops`
- `telegram.thread.alert`

Resolution priority:
1. Explicit config/env (`TELEGRAM_THREAD_*`)
2. Learned dynamic binding (DB runtime settings)
3. Category fallback (`ops` -> last operator thread -> default chat)

## Telegram UX Design

### Primary Control Surface

Add `/control` command that renders one status card with inline buttons.

Card fields:
- Mode
- Bot state
- Deployer mode
- Auto rule
- Thread bind status

### Inline Controls

Buttons:
- `Mode: Review/Auto` (toggle)
- `Bot: On/Off` (toggle)
- `Deployer: Clanker/Bankr/Both` (cycle)
- `Threads: Smart Bind` (bind categories from current active thread)
- `Refresh`

### Slash Command Companions

- `/setmode review|auto`
- `/setbot on|off`
- `/setdeployer clanker|bankr|both`
- `/control`
- `/manualdeploy` (wizard entrypoint)
- `/deploynow <platform> <name> <symbol> <image_or_cid> [description]` (power-user direct mode)
- `/deployca <platform> <candidate_id>` (force deploy from existing candidate)

Backward-compatible operational commands remain:
- `/status`, `/queue`, `/candidate`, `/deploys`, `/claimfees`

### Manual Deploy UX (Hybrid)

Manual deploy uses a hybrid interaction model:
- Inline buttons for fast decisions (platform, source mode, confirm/cancel)
- Text input for high-entropy fields (name, symbol, description, candidate id, override CID)

Rationale:
- Buttons reduce typo risk and operator latency during active windows.
- Text input keeps flexibility for custom token metadata.

## Manual Deploy via Chat

### Functional Requirements

1. Operator can deploy manually without waiting for detector/review queue.
2. Operator can select deploy mode: `clanker`, `bankr`, or `both`.
3. Operator can deploy from:
- Existing `candidate_id`, or
- Fully custom metadata entered in chat.
4. All manual deploys require explicit final confirmation.

### Wizard Flow (`/manualdeploy`)

1. Select platform mode (`clanker|bankr|both`).
2. Select source (`candidate` or `custom`).
3. Build draft payload (collect metadata and image/CID).
4. Show preview card (name, symbol, image CID, tax/admin/reward config, deployer mode).
5. Final action: `Confirm Deploy` or `Cancel`.

### Manual Deploy Data Model

Add table `manual_deploy_requests`:
- `id`
- `requested_by_chat_id`
- `requested_by_user_id`
- `thread_id`
- `source_mode` (`candidate|custom`)
- `candidate_id` nullable
- `platform_mode` (`clanker|bankr|both`)
- `payload_json`
- `status` (`draft|confirmed|deploying|completed|failed|cancelled`)
- `created_at`
- `updated_at`

### Manual Deploy Guardrails

- Authorized chat enforcement.
- Confirm-required execution (no single-tap transaction send).
- Rate limit per window to avoid accidental spam.
- Strict payload validation before confirmation.
- If selected deployer is unavailable, block with explicit operator message.

## Runtime Behavior

### Review vs Auto

- `review`: current behavior (manual approve/reject gate).
- `auto`: only `priority_review` candidates move to deploy without manual callback.

### Bot On/Off

- `on`: normal notification behavior.
- `off`: suppress non-critical info messages; keep critical alert/failure messages enabled.

### Deployer Mode

- `clanker`: single deploy path via Clanker.
- `bankr`: single deploy path via Bankr.
- `both`: execute both deployers for same candidate in one orchestrated flow.

## Multi-Deployer Execution Design

### Deployer Contract

Introduce deployer abstraction with one interface:
- `deploy(request) -> DeployResult`
- platform identifier (`clanker` or `bankr`)

### Orchestration

At deploy stage:
- resolve current `ops.deployer_mode`
- run deployer(s) with per-platform timeout and isolated error capture
- store result rows per platform

For `both`:
- execute `clanker` then `bankr` (deterministic, easier tracing)
- failure in one platform must not cancel the other

### Persistence Changes

Extend `deployment_results` with `platform` column.

Behavior:
- one row per candidate per platform attempt
- status aggregation for operator summary:
  - `partial_success`: one success, one fail
  - `success`: all selected deployers succeed
  - `failed`: all selected deployers fail

## Thread Adaptation Strategy (Future-Proof for Bankr)

Category mapping:
- `review`: approval cards
- `deploy`: deploy lifecycle updates (all platforms)
- `claim`: claim-fee outputs
- `ops`: command/control responses
- `alert`: failures and critical errors

For `both` mode:
- keep one deploy thread; include platform sections in message body.
- do not split by platform thread in this phase to reduce operator cognitive load.

Manual deploy threading:
- Wizard interaction in `ops` thread.
- Deploy execution updates in `deploy` thread.
- Critical failures in `alert` thread.

## Telegram Media to IPFS Flow (Deploy-Ready)

### Operator Requirement

Operator can send image directly to bot, and bot converts it automatically into deploy-ready IPFS CID.

### Processing Flow

1. Operator uploads image (`photo` or `document`) in Telegram.
2. Bot retrieves file via Telegram Bot API (`getFile` + file download).
3. Validate type/size and decode integrity.
4. Normalize image for deployment:
- standard output format (default PNG)
- optional resize constraints for efficiency
- strip non-essential metadata
5. Compute content hash for dedupe.
6. Reuse CID from cache when hash already exists; otherwise upload to Pinata.
7. Return `ipfs://<CID>` and attach to draft/manual deploy payload.

### Reliability Rules

- Retry Pinata upload with bounded backoff.
- Deterministic fallback choices on failure:
- retry upload
- use previous CID
- cancel flow
- Reject unsupported or corrupted files with actionable error text.

### Storage

Persist media upload mapping in cache/DB:
- content hash
- CID
- mime type
- byte size
- timestamp

## Safety and Consistency

- All setting changes are authorized by chat id check.
- Callback handlers are idempotent and race-tolerant.
- Runtime setting writes are atomic (`UPSERT`).
- Resolver reads are lightweight and safe on restart.
- If selected deployer is unavailable (e.g., Bankr not configured), fallback to `clanker` with explicit warning in ops/alert thread.

## Rollout Plan

1. Runtime settings API completion and tests.
2. Telegram control panel + callbacks + slash setters.
3. Pipeline/deploy worker runtime resolution (`review/auto`, `deployer_mode`).
4. Multi-deployer persistence + message formatting.
5. Backward compatibility validation + migration notes.

## Testing Strategy

Unit tests:
- runtime setting read/write and default fallback
- control callback transitions and idempotency
- resolver behavior for mode/bot/deployer
- thread smart binding precedence
- deployer-mode orchestration (`clanker`, `bankr`, `both`)

Integration tests:
- end-to-end candidate in `review` mode
- end-to-end candidate in `auto` mode (`priority_review` only)
- `both` mode with one deployer failure still producing partial success

Regression tests:
- existing slash commands still function
- existing Clanker-only flow unchanged by default

## Backward Compatibility

Default runtime values:
- `ops.mode=review`
- `ops.bot_enabled=on`
- `ops.deployer_mode=clanker`
- `ops.auto_rule=priority_review_only`

With defaults above, current production behavior remains unchanged.
