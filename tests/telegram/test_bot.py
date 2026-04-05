import pytest

from clankandclaw.telegram.bot import build_review_message, AIOGRAM_AVAILABLE

try:
    from clankandclaw.telegram.bot import build_review_keyboard
except ImportError:
    build_review_keyboard = None  # type: ignore


def test_build_review_message_contains_required_fields():
    msg = build_review_message(
        "sig-1", "priority_review", 85, ["deploy_keyword", "base_context"],
        raw_text="deploy token Pepe symbol PEPE",
        source="x",
        context_url="https://x.com/alice/status/1",
        author_handle="alice",
    )
    assert "sig-1" in msg
    assert "priority_review" in msg
    assert "85" in msg
    assert "deploy_keyword" in msg


def test_build_review_message_includes_raw_text():
    msg = build_review_message(
        "sig-1", "review", 50, [],
        raw_text="deploy token Moon symbol MOON",
    )
    assert "deploy token Moon" in msg


def test_build_review_message_truncates_long_raw_text():
    long_text = "x" * 500
    msg = build_review_message("sig-1", "review", 50, [], raw_text=long_text)
    assert "…" in msg
    assert "x" * 500 not in msg


def test_build_review_message_includes_source_label():
    assert "X / Twitter" in build_review_message("sig-1", "review", 50, [], source="x")
    assert "GMGN" in build_review_message("sig-1", "review", 50, [], source="gmgn")


def test_build_review_message_includes_context_url():
    msg = build_review_message(
        "sig-1", "review", 50, [],
        context_url="https://x.com/alice/status/1",
    )
    assert "https://x.com/alice/status/1" in msg


def test_build_review_message_includes_author_handle():
    msg = build_review_message("sig-1", "review", 50, [], author_handle="alice")
    assert "@alice" in msg


def test_build_review_message_omits_optional_fields_when_absent():
    msg = build_review_message("sig-1", "review", 50, [])
    assert "blockquote" not in msg


def test_build_review_message_priority_emojis():
    assert "🔥" in build_review_message("sig-1", "priority_review", 90, [])
    assert "📋" in build_review_message("sig-1", "review", 50, [])


@pytest.mark.skipif(not AIOGRAM_AVAILABLE, reason="aiogram not installed")
def test_build_review_keyboard_has_approve_and_reject():
    keyboard = build_review_keyboard("sig-1")
    assert len(keyboard.inline_keyboard) == 1
    row = keyboard.inline_keyboard[0]
    assert len(row) == 2
    cb_data = {btn.callback_data for btn in row}
    assert "approve:sig-1" in cb_data
    assert "reject:sig-1" in cb_data
