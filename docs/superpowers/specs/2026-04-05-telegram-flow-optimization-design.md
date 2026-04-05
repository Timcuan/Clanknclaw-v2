# Telegram Flow Optimization Design (2026-04-05)

## Goal

Optimize Telegram operator experience for 24/7 manual operations while keeping strict approve/reject control per candidate.

## Scope

In scope:
- Inline `Approve/Reject` flow hardening
- Slash command simplification and modernization
- Better callback safety/idempotency behavior
- Better operator-facing status messages
- Test updates for the new flow

Out of scope:
- Auto-deploy or score-based auto-approval
- Multi-operator role/permission system
- Major database schema redesign

## Current Problems

- Command set is redundant (`/candidates`, `/stats`, `/deployments`) and less operator-friendly.
- Callback behavior is functional but can be improved for stale clicks, duplicate actions, and access safety.
- Operator flow lacks a focused “queue-first” command language.

## Proposed Design

### 1. Inline Approve/Reject Flow (Primary Path)

- Keep one review message per candidate with inline buttons:
  - `approve:<candidate_id>`
  - `reject:<candidate_id>`
- On click:
  - Validate callback source chat (must match configured operator chat).
  - Attempt idempotent transition:
    - Approve path: lock review item (`pending -> deploying`)
    - Reject path: reject review item (`pending -> rejected`)
  - Success:
    - Edit original review message into final state summary
    - Send callback acknowledgement
  - Already processed/expired:
    - Do not throw noisy exceptions to operator
    - Return concise callback alert: “already processed or expired”

### 2. Notification Timeline

- Keep stage notifications separated for clarity:
  - `Preparing Deploy`
  - `Deploy Success`
  - `Deploy Failure`
- Inline message edit provides immediate decision state.
- Deploy stage notifications provide async execution timeline.

### 3. Slash Command Menu (New)

Primary commands:
- `/start` — short intro + command map
- `/help` — concise usage and examples
- `/status` — compact operational counters (pending/deployed/failed/rejected)
- `/queue` — pending review queue (max 10, compact)
- `/candidate <id>` — one-candidate detail view
- `/deploys` — recent deployment outcomes

Compatibility strategy:
- Keep old commands as aliases to avoid operator breakage:
  - `/candidates` -> `/queue`
  - `/deployments` -> `/deploys`
  - `/stats` -> `/status`
- `/cancel <id>` remains available but no longer promoted in the default menu.

## Data & Logic Changes

- Reuse existing DB APIs where possible:
  - `list_pending_reviews()`
  - `get_candidate()`
  - `get_stats()`
  - `list_recent_deployments()`
  - `reject_review_item()`
- Add minimal helper formatting in Telegram bot layer for:
  - candidate detail response
  - compact queue/deploy lines
- No new schema migration required for this phase.

## Error Handling

- Callback path:
  - Distinguish user-facing error (`already processed or expired`) vs internal error (logged).
- Command path:
  - Return short, safe messages on DB failures.
  - Keep full traceback only in logs.
- Keep bot resilient; no crash on single command/callback failure.

## Testing Plan

Update/add tests for:
- inline approve/reject idempotency behavior
- stale callback behavior (already processed)
- `/queue` output behavior
- `/candidate <id>` behavior (found/not found/bad input)
- alias commands mapping behavior
- `/status` and `/deploys` output still correct

Regression:
- existing deploy notification tests must remain passing
- existing worker lifecycle tests remain unchanged

## Rollout

1. Implement bot command refactor with aliases.
2. Harden callback handling and user-facing alerts.
3. Update tests.
4. Run full test suite.
5. Update quick docs for command usage.

## Acceptance Criteria

- Operator can run queue-first workflow with `/queue` + inline buttons only.
- Duplicate or stale button clicks do not create errors/noisy failures.
- Slash menu is compact and consistent with latest flow.
- Full test suite passes.
