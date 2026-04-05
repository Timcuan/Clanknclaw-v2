import pytest


def test_build_review_message_contains_priority_and_candidate_id():
    """Test that review message contains required information."""
    from clankandclaw.telegram.bot import build_review_message
    
    text = build_review_message("sig-1", "priority_review", 85, ["deploy_keyword", "base_context"])
    assert "sig-1" in text
    assert "priority_review" in text
    assert "85" in text
    assert "deploy_keyword" in text


def test_build_review_keyboard_has_approve_and_reject():
    """Test that review keyboard has approve and reject buttons."""
    try:
        from clankandclaw.telegram.bot import build_review_keyboard, AIOGRAM_AVAILABLE
    except ImportError:
        pytest.skip("aiogram not installed")
    
    if not AIOGRAM_AVAILABLE:
        pytest.skip("aiogram not installed")
    
    keyboard = build_review_keyboard("sig-1")
    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 1
    assert len(keyboard.inline_keyboard[0]) == 2
    
    approve_button = keyboard.inline_keyboard[0][0]
    reject_button = keyboard.inline_keyboard[0][1]
    
    assert "Approve" in approve_button.text
    assert "approve:sig-1" == approve_button.callback_data
    
    assert "Reject" in reject_button.text
    assert "reject:sig-1" == reject_button.callback_data
