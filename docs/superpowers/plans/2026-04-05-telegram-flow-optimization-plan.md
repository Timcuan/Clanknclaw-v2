# Telegram Flow Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a queue-first Telegram operator flow with robust manual approve/reject and simplified slash commands.

**Architecture:** Keep inline callback actions as the source of truth for decision transitions, move operator navigation to a compact slash command set, and keep compatibility aliases for old commands. Implement behavior in `telegram/bot.py`, keep orchestration in `core/workers/telegram_worker.py`, and verify via focused bot/worker tests.

**Tech Stack:** Python 3.11, aiogram, SQLite-backed DatabaseManager, pytest.

---

### Task 1: Add New Slash Command Surface

**Files:**
- Modify: `clankandclaw/telegram/bot.py`
- Test: `tests/telegram/test_bot.py`

- [ ] Add `/queue`, `/candidate`, `/deploys` command handlers.
- [ ] Keep `/candidates`, `/deployments`, `/stats` as aliases.
- [ ] Update `/start` and `/help` command text to the new operator menu.
- [ ] Add/adjust unit tests for message builder and command-facing formatting helpers.

### Task 2: Harden Callback Logic

**Files:**
- Modify: `clankandclaw/telegram/bot.py`
- Modify: `clankandclaw/core/workers/telegram_worker.py`
- Test: `tests/core/test_telegram_worker.py`

- [ ] Add callback chat guard (configured operator chat only).
- [ ] Convert stale/duplicate callback outcomes to concise operator-facing alerts.
- [ ] Keep internal logs detailed while user-facing messages remain concise.
- [ ] Add worker tests for idempotent callback failure semantics.

### Task 3: Add Candidate Detail Response

**Files:**
- Modify: `clankandclaw/telegram/bot.py`
- Modify: `clankandclaw/database/manager.py` (if minimal read helper is needed)
- Test: `tests/telegram/test_bot.py`, `tests/database/test_manager.py` (if DB helper added)

- [ ] Implement `/candidate <id>` detail rendering from existing DB reads.
- [ ] Ensure fallback behavior for missing ID and not-found candidate.
- [ ] Test parser/formatting behavior.

### Task 4: Validate End-to-End Bot/Worker Behavior

**Files:**
- Modify: `tests/telegram/test_bot.py`
- Modify: `tests/core/test_telegram_worker.py`

- [ ] Add regression tests for queue/deploys alias compatibility.
- [ ] Verify old commands still work as aliases and no lifecycle regressions.
- [ ] Run full test suite and fix failures.

