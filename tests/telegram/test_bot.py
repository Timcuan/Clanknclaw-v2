import json

import pytest

from clankandclaw.telegram.bot import (
    AIOGRAM_AVAILABLE,
    _parse_command_args,
    build_forum_topic_plan,
    build_action_callback_data,
    build_candidate_detail_message,
    build_deploys_message,
    build_queue_message,
    build_review_message,
    resolve_authorized_chat_id,
)

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
    assert "Review Candidate" not in msg
    assert "ID:" not in msg
    assert "85" in msg
    assert "deploy keyword" in msg
    assert "Market" in msg


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
    assert "Farcaster" in build_review_message("sig-1", "review", 50, [], source="farcaster")
    assert "GeckoTerminal" in build_review_message("sig-1", "review", 50, [], source="gecko")


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
    assert "Signals:</b> —" in msg
    assert "blockquote" not in msg
    assert "Author:</b>" not in msg


def test_build_review_message_priority_emojis():
    assert "🔥" in build_review_message("sig-1", "priority_review", 90, [])
    assert "📋" in build_review_message("sig-1", "review", 50, [])


def test_build_review_message_uses_chain_icon():
    msg = build_review_message(
        "sig-1",
        "review",
        50,
        [],
        metadata={"network": "base", "token_name": "Moon", "token_symbol": "MOON"},
    )
    assert msg.startswith("🔵")


def test_build_queue_message_compact_list():
    rows = [
        {
            "candidate_id": "x-1",
            "score": 88,
            "source": "x",
            "reason_codes": "deploy_keyword,base_context",
        },
        {
            "candidate_id": "g-1",
            "score": None,
            "source": "gecko",
            "reason_codes": "",
        },
    ]
    msg = build_queue_message(rows)
    assert "Pending Queue" in msg
    assert "Total: <b>2</b>" in msg
    assert "x-1" in msg
    assert "88" in msg
    assert "deploy_keyword" in msg
    assert "g-1" in msg
    assert "score ?" in msg


def test_build_queue_message_empty():
    assert "No pending reviews." in build_queue_message([])


def test_build_candidate_detail_message_with_full_data():
    candidate = {
        "id": "x-1",
        "source": "x",
        "raw_text": "deploy token ALPHA",
        "metadata_json": json.dumps(
            {
                "author_handle": "alice",
                "context_url": "https://x.com/alice/status/1",
            }
        ),
    }
    decision = {
        "score": 91,
        "decision": "priority_review",
        "reason_codes": "deploy_keyword,base_context",
        "recommended_platform": "clanker",
    }
    review = {"status": "pending"}
    deploy = {"status": "deploy_success", "contract_address": "0x" + "a" * 40}

    msg = build_candidate_detail_message(candidate, decision, review, deploy)
    assert "Candidate Detail" in msg
    assert "x-1" in msg
    assert "@alice" in msg
    assert "priority_review" in msg
    assert "pending" in msg
    assert "deploy_success" in msg
    assert "0x" + "a" * 40 in msg


def test_build_candidate_detail_message_handles_missing_optional_data():
    candidate = {
        "id": "x-2",
        "source": "gecko",
        "raw_text": "launch token BETA",
        "metadata_json": "{}",
    }
    msg = build_candidate_detail_message(candidate, None, None, None)
    assert "x-2" in msg
    assert "n/a" in msg


def test_build_candidate_detail_message_escapes_raw_text():
    candidate = {
        "id": "x-3",
        "source": "x",
        "raw_text": "<b>bad</b> & \"raw\"",
        "metadata_json": "{}",
    }
    msg = build_candidate_detail_message(candidate, None, None, None)
    assert "<blockquote>&lt;b&gt;bad&lt;/b&gt; &amp; &quot;raw&quot;</blockquote>" in msg


def test_build_deploys_message_compact_success_and_failure():
    rows = [
        {
            "candidate_id": "x-1",
            "status": "deploy_success",
            "contract_address": "0x" + "a" * 40,
            "tx_hash": "0x" + "b" * 64,
            "error_code": None,
            "error_message": None,
        },
        {
            "candidate_id": "x-2",
            "status": "deploy_failed",
            "contract_address": None,
            "tx_hash": None,
            "error_code": "sdk_error",
            "error_message": "gas estimation failed",
        },
    ]
    msg = build_deploys_message(rows)
    assert "Recent Deployments" in msg
    assert "Total: <b>2</b>" in msg
    assert "x-1" in msg
    assert "0x" + "a" * 40 in msg
    assert "0x" + "b" * 64 in msg
    assert "x-2" in msg
    assert "sdk_error" in msg
    assert "gas estimation failed" in msg


def test_build_deploys_message_truncates_long_error():
    rows = [
        {
            "candidate_id": "x-9",
            "status": "deploy_failed",
            "contract_address": None,
            "tx_hash": None,
            "error_code": "sdk_error",
            "error_message": "x" * 200,
        }
    ]
    msg = build_deploys_message(rows)
    assert "x-9" in msg
    assert "…" in msg
    assert ("x" * 200) not in msg


def test_build_deploys_message_empty():
    assert "No deployments yet." in build_deploys_message([])


def test_parse_command_args_supports_quoted_values():
    args = _parse_command_args('/deploynow clanker "Moon Coin" MOON auto "launch alpha"')
    assert args == ["clanker", "Moon Coin", "MOON", "auto", "launch alpha"]


def test_parse_command_args_returns_empty_on_unbalanced_quotes():
    assert _parse_command_args('/deploynow clanker "Moon Coin MOON auto') == []


def test_build_action_callback_data_rejects_over_limit_without_encoder():
    long_candidate_id = "gecko-solana:46mhiYcNiWZ5ymenbSrReJui8qsJynAM9nbeuLY4oH4A"
    with pytest.raises(ValueError, match="callback_data"):
        build_action_callback_data("approve", long_candidate_id)


def test_resolve_authorized_chat_id_prefers_runtime_pairing():
    assert resolve_authorized_chat_id("1558397457", "-1001234567890") == "-1001234567890"


def test_resolve_authorized_chat_id_falls_back_to_configured():
    assert resolve_authorized_chat_id("1558397457", None) == "1558397457"
    assert resolve_authorized_chat_id("1558397457", "   ") == "1558397457"


def test_build_forum_topic_plan_includes_all_when_empty():
    plan = build_forum_topic_plan({})
    assert plan == [
        ("review", "cnc-review"),
        ("deploy", "cnc-deploy"),
        ("claim", "cnc-claim"),
        ("ops", "cnc-ops"),
        ("alert", "cnc-alert"),
    ]


def test_build_forum_topic_plan_skips_bound_categories():
    plan = build_forum_topic_plan({"ops": 123, "review": 456})
    categories = [category for category, _ in plan]
    assert "ops" not in categories
    assert "review" not in categories
    assert "deploy" in categories


@pytest.mark.skipif(not AIOGRAM_AVAILABLE, reason="aiogram not installed")
def test_build_review_keyboard_has_approve_and_reject():
    keyboard = build_review_keyboard("sig-1")
    assert len(keyboard.inline_keyboard) == 1
    cb_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
    assert "approve:sig-1" in cb_data
    assert "detail:sig-1" in cb_data


@pytest.mark.skipif(not AIOGRAM_AVAILABLE, reason="aiogram not installed")
def test_build_review_keyboard_supports_encoded_candidate_id():
    long_candidate_id = "gecko-solana:46mhiYcNiWZ5ymenbSrReJui8qsJynAM9nbeuLY4oH4A"
    keyboard = build_review_keyboard(long_candidate_id, encode_candidate_id=lambda _: "k:abc123")
    cb_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
    assert "approve:k:abc123" in cb_data
    assert "detail:k:abc123" in cb_data
    assert max(len(item or "") for item in cb_data) <= 64
