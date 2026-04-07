import pytest

import clankandclaw.utils.llm as llm


@pytest.mark.asyncio
async def test_get_gemini_client_reuses_singleton_client():
    c1 = await llm._get_gemini_client()
    c2 = await llm._get_gemini_client()
    assert c1 is c2


@pytest.mark.asyncio
async def test_reset_gemini_client_recreates_client():
    c1 = await llm._get_gemini_client()
    await llm._reset_gemini_client_for_tests()
    c2 = await llm._get_gemini_client()
    assert c1 is not c2
    await llm._reset_gemini_client_for_tests()


def test_daily_budget_guard_blocks_after_limit(monkeypatch: pytest.MonkeyPatch):
    guard = llm.DailyBudgetGuard(default_limit_per_day=2)
    monkeypatch.setenv("GEMINI_DAILY_REQUEST_LIMIT", "2")
    assert guard.allow_next() is True
    assert guard.allow_next() is True
    assert guard.allow_next() is False
