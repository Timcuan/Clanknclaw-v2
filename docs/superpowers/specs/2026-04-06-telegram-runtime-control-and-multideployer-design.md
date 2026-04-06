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

Backward-compatible operational commands remain:
- `/status`, `/queue`, `/candidate`, `/deploys`, `/claimfees`

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
