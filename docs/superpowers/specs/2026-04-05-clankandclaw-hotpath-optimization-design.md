# Clank&Claw Hot-Path Optimization Design

## Goal

Run a focused optimization pass on the existing tools without changing the overall architecture. The target is lower latency and less wasted work across three user-visible paths:

1. `signal -> Telegram review`
2. `approve -> deploy submit`
3. `signal -> deploy submit`

This pass is not a general correctness rewrite. It is a bounded audit of hot-path work, followed by small defensive patches that keep the current flow intact and preserve the existing test gate.

## Scope

In scope:

- Audit detector, pipeline, Telegram, and deploy-preparation hot paths for unnecessary work
- Remove or defer non-essential blocking steps
- Tighten low-cost validation that prevents slow failures later in the path
- Add lightweight timing and clearer step-level failure surfaces where they help isolate latency
- Add or adjust tests only when behavior relevant to the hot path changes

Out of scope:

- New architecture
- Queue or worker topology changes
- Large refactors
- General product-feature work
- Broad correctness audit outside latency-related surfaces

## Optimization Strategy

Use a `hotspot-only` optimization pass with minimal instrumentation.

The sequence is:

1. Audit `signal -> Telegram`
2. Audit `approve -> deploy submit`
3. Re-check `signal -> deploy submit`
4. Patch only the highest-value hotspots that do not require structural changes

Every audited step is classified as one of:

- `required`: must stay in the current critical path
- `optional`: useful but movable outside the critical path
- `misplaced`: currently happens too early, too often, or in the wrong layer

Each hotspot is then tagged by waste type:

- `too_early`: work happens before the flow knows it needs the result
- `duplicate`: the same data or transformation is repeated
- `blocking`: non-essential work holds the fast path open

## Path 1: Signal To Telegram

### Audit focus

- detector normalization
- filter and scoring work
- routing and persistence work before review creation
- Telegram message assembly

### Design decisions

- Keep detector normalization cheap and deterministic
- Avoid any enrichment before a candidate has cleared filter and scoring
- Avoid duplicate serialization or reconstruction before the review message is sent
- Keep review message formatting lightweight and bounded in size
- Persist only what the next stage needs immediately

### Expected patch shape

- trim review-message inputs to the fields already needed by the operator
- avoid extra formatting work for text that is never displayed
- collapse repeated hot-path data access where the same candidate fields are read multiple times
- add low-noise timing markers around normalization, scoring, persistence, and review-message build

## Path 2: Approve To Deploy Submit

### Audit focus

- candidate lookup and reconstruction
- token identity extraction
- image fetch and IPFS upload
- deploy request construction
- preflight checks

### Design decisions

- Keep metadata artifact generation out of the blocking deploy path unless the deployer strictly requires it
- Fail fast on missing image inputs and malformed token identity inputs before network work
- Prefer using already-available candidate fields over reconstructing or recomputing them
- Keep preflight in place, but ensure it receives only the minimal final payload

### Expected patch shape

- remove dead metadata work and dead imports left behind after metadata removal
- classify deploy-preparation failures by step so slow failures are easy to spot
- add step timing for extraction, image fetch, IPFS upload, and preflight
- normalize token name and symbol once at preparation time, not in downstream layers
- make candidate rehydration cheaper if the current flow still reconstructs data only to reuse it immediately

## Path 3: Signal To Deploy Submit

### Audit focus

- combined effect of the two optimized paths above
- hidden blocking work between review approval and deploy preparation

### Design decisions

- do not chase a separate end-to-end redesign
- treat this path as a verification pass after the first two paths are cleaned up
- only patch combined-path hotspots that remain visible after the targeted fixes

## Module Priorities

Primary priority:

- `/Users/aaa/Projects/clank and claw v2/clankandclaw/core/deploy_preparation.py`
- `/Users/aaa/Projects/clank and claw v2/clankandclaw/core/pipeline.py`
- `/Users/aaa/Projects/clank and claw v2/clankandclaw/telegram/bot.py`

Secondary priority:

- detector worker or normalization code involved in review creation
- hot-path model surfaces only if a model field is now dead or forces unnecessary work

## Acceptance Criteria

- No architecture changes
- Existing test suite remains green
- Hot-path patches are small and localized
- Blocking metadata work stays out of the deploy-preparation path
- Review creation and deploy preparation expose clearer latency boundaries than before
- Any changed behavior in the hot path is locked by targeted tests

## Verification

Required verification after implementation:

- targeted pytest runs for each touched module
- full `pytest -v`
- manual diff review to confirm no out-of-scope contract drift

## Risks And Guardrails

Main risk:

- shaving latency by deleting checks that actually protect the deploy path

Guardrails:

- keep deterministic filter/scoring logic intact
- keep preflight intact
- do not remove persistence needed for idempotency or operator state
- prefer moving optional work out of the path over deleting it without evidence

## Implementation Notes

This optimization pass should assume the current repo state already includes:

- Telegram operator controls
- DB schema upgrades
- earlier audit fixes already landed in recent commits

So the goal is not to recreate those changes. The goal is to finish the remaining hot-path cleanup with the smallest useful set of edits.
