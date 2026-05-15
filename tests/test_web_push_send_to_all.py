"""RED tests for WU-3 — web_push.send_to_all.

Tests run BEFORE the implementation exists; they must all fail (ImportError).
Once the green commit lands, all tests here must pass.

Spec: REQ-4, SC-4.7–4.9
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_remote.db.push_subscriptions import PushSubscription

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sub(endpoint: str, n: int = 1) -> PushSubscription:
    return PushSubscription(
        id=n,
        endpoint=endpoint,
        p256dh=f"p256dh-{n}",
        auth=f"auth-{n}",
        user_agent=None,
        created_at="2026-01-01T00:00:00+00:00",
        last_seen_at=None,
    )


def _make_subs_repo(subs: list[PushSubscription]) -> MagicMock:
    repo = MagicMock()
    repo.list_all.return_value = subs
    repo.delete_by_endpoint.return_value = True
    return repo


_FAKE_PRIV = "-----BEGIN PRIVATE KEY-----\nfake"


def _make_vapid_repo(pub: str = "BTestPublicKey", priv: str = _FAKE_PRIV) -> MagicMock:
    from claude_remote.db.vapid_keys import VapidKeys

    repo = MagicMock()
    repo.get.return_value = VapidKeys(
        id=1,
        public_key=pub,
        private_key=priv,
        created_at="2026-01-01T00:00:00+00:00",
    )
    return repo


# ---------------------------------------------------------------------------
# send_to_all
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_to_all_returns_empty_list_no_subs() -> None:
    """send_to_all returns [] when list_all() is empty. (SC-4.8)"""
    from claude_remote.services.web_push import send_to_all

    subs_repo = _make_subs_repo([])
    vapid_repo = _make_vapid_repo()

    result = await send_to_all(subs_repo, vapid_repo, title="T", body="B")
    assert result == []
    subs_repo.delete_by_endpoint.assert_not_called()


@pytest.mark.anyio
async def test_send_to_all_calls_send_push_once_per_sub() -> None:
    """send_to_all calls send_push exactly once per subscription. (SC-4.7)"""
    from claude_remote.services import web_push
    from claude_remote.services.web_push import SendPushResult, send_to_all

    subs = [_make_sub("https://ep1"), _make_sub("https://ep2", 2)]
    subs_repo = _make_subs_repo(subs)
    vapid_repo = _make_vapid_repo()

    send_push_mock = AsyncMock(return_value=SendPushResult.OK)
    with patch.object(web_push, "send_push", send_push_mock):
        result = await send_to_all(subs_repo, vapid_repo, title="T", body="B")

    assert send_push_mock.call_count == 2
    assert result.count(SendPushResult.OK) == 2


@pytest.mark.anyio
async def test_send_to_all_deletes_expired_endpoint() -> None:
    """send_to_all calls delete_by_endpoint for EXPIRED subs, not for OK. (SC-4.7)"""
    from claude_remote.services import web_push
    from claude_remote.services.web_push import SendPushResult, send_to_all

    s1 = _make_sub("https://ep-ok", 1)
    s2 = _make_sub("https://ep-expired", 2)
    s3 = _make_sub("https://ep-ok2", 3)
    subs_repo = _make_subs_repo([s1, s2, s3])
    vapid_repo = _make_vapid_repo()

    # s2 is EXPIRED, s1 and s3 are OK
    async def _mock_send(sub_info: dict, priv: str, title: str, body: str, data=None, ttl: int = 60):  # noqa: E501  # type: ignore[no-untyped-def]
        if sub_info["endpoint"] == "https://ep-expired":
            return SendPushResult.EXPIRED
        return SendPushResult.OK

    with patch.object(web_push, "send_push", side_effect=_mock_send):
        result = await send_to_all(subs_repo, vapid_repo, title="T", body="B")

    subs_repo.delete_by_endpoint.assert_called_once_with("https://ep-expired")
    assert SendPushResult.EXPIRED in result
    assert result.count(SendPushResult.OK) == 2


@pytest.mark.anyio
async def test_send_to_all_does_not_raise_on_all_failed() -> None:
    """send_to_all returns [FAILED, FAILED] without raising. (SC-4.9)"""
    from claude_remote.services import web_push
    from claude_remote.services.web_push import SendPushResult, send_to_all

    subs = [_make_sub("https://ep1"), _make_sub("https://ep2", 2)]
    subs_repo = _make_subs_repo(subs)
    vapid_repo = _make_vapid_repo()

    send_push_mock = AsyncMock(return_value=SendPushResult.FAILED)
    with patch.object(web_push, "send_push", send_push_mock):
        result = await send_to_all(subs_repo, vapid_repo, title="T", body="B")

    assert result == [SendPushResult.FAILED, SendPushResult.FAILED]
    subs_repo.delete_by_endpoint.assert_not_called()


@pytest.mark.anyio
async def test_send_to_all_does_not_raise_when_list_all_fails() -> None:
    """send_to_all returns [] without raising when list_all() throws."""
    from claude_remote.services.web_push import send_to_all

    subs_repo = MagicMock()
    subs_repo.list_all.side_effect = RuntimeError("DB error")
    vapid_repo = _make_vapid_repo()

    result = await send_to_all(subs_repo, vapid_repo, title="T", body="B")
    assert result == []


@pytest.mark.anyio
async def test_send_to_all_does_not_raise_when_vapid_get_fails() -> None:
    """send_to_all returns [] without raising when vapid_repo.get() throws."""
    from claude_remote.services.web_push import send_to_all

    subs = [_make_sub("https://ep1")]
    subs_repo = _make_subs_repo(subs)
    vapid_repo = MagicMock()
    vapid_repo.get.side_effect = RuntimeError("vapid missing")

    result = await send_to_all(subs_repo, vapid_repo, title="T", body="B")
    assert result == []


@pytest.mark.anyio
async def test_send_to_all_returns_mixed_results() -> None:
    """send_to_all returns list of all results (OK, EXPIRED, FAILED) in order."""
    from claude_remote.services import web_push
    from claude_remote.services.web_push import SendPushResult, send_to_all

    s1 = _make_sub("https://ep1", 1)
    s2 = _make_sub("https://ep2", 2)
    s3 = _make_sub("https://ep3", 3)
    subs_repo = _make_subs_repo([s1, s2, s3])
    vapid_repo = _make_vapid_repo()

    results_map = {
        "https://ep1": SendPushResult.OK,
        "https://ep2": SendPushResult.EXPIRED,
        "https://ep3": SendPushResult.FAILED,
    }

    async def _mock_send(sub_info: dict, priv: str, title: str, body: str, data=None, ttl: int = 60):  # noqa: E501  # type: ignore[no-untyped-def]
        return results_map[sub_info["endpoint"]]

    with patch.object(web_push, "send_push", side_effect=_mock_send):
        result = await send_to_all(subs_repo, vapid_repo, title="T", body="B")

    assert SendPushResult.OK in result
    assert SendPushResult.EXPIRED in result
    assert SendPushResult.FAILED in result
    assert len(result) == 3


@pytest.mark.anyio
async def test_send_to_all_vapid_claims_in_send_push_call() -> None:
    """send_to_all passes vapid private_key from vapid_repo.get() to send_push."""
    from claude_remote.services import web_push
    from claude_remote.services.web_push import SendPushResult, send_to_all

    subs = [_make_sub("https://ep1")]
    subs_repo = _make_subs_repo(subs)
    vapid_repo = _make_vapid_repo(priv="-----BEGIN PRIVATE KEY-----\nspecific-key")

    send_push_mock = AsyncMock(return_value=SendPushResult.OK)
    with patch.object(web_push, "send_push", send_push_mock):
        await send_to_all(subs_repo, vapid_repo, title="T", body="B")

    call_args = send_push_mock.call_args
    # Second positional argument is the vapid_private_pem
    assert "specific-key" in call_args.args[1]
