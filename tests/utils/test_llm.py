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
