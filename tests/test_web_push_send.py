"""RED tests for WU-3 — web_push.send_push.

Tests run BEFORE the implementation exists; they must all fail (ImportError).
Once the green commit lands, all tests here must pass.

Spec: REQ-4, SC-4.1–4.6
Mock seam: unittest.mock.patch('claude_remote.services.web_push.webpush')
"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
from py_vapid import Vapid
from pywebpush import WebPushException

from claude_remote.services.vapid_keygen import generate_keypair

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENDPOINT = "https://fcm.googleapis.com/fcm/send/test123"
_P256DH = "BGy7base64key"
_AUTH = "authsecret"
# Real PEM: pywebpush only accepts a Vapid object or a from_string-compatible
# encoding. A fake PEM never round-trips, so use a genuine generated keypair.
_PUBLIC_KEY, _PRIVATE_PEM = generate_keypair()

_SUBSCRIPTION_INFO = {
    "endpoint": _ENDPOINT,
    "keys": {"p256dh": _P256DH, "auth": _AUTH},
}


def _make_webpush_exc(status_code: int | None) -> WebPushException:
    """Build a WebPushException with a stub .response.status_code."""
    exc = WebPushException("test push error")
    if status_code is not None:
        exc.response = Mock()  # type: ignore[attr-defined]
        exc.response.status_code = status_code
    return exc


# ---------------------------------------------------------------------------
# send_push — result enum
# ---------------------------------------------------------------------------


class TestSendPushResultEnum:
    def test_send_push_result_has_ok(self) -> None:
        """SendPushResult must have OK member."""
        from claude_remote.services.web_push import SendPushResult

        assert SendPushResult.OK is not None

    def test_send_push_result_has_expired(self) -> None:
        """SendPushResult must have EXPIRED member."""
        from claude_remote.services.web_push import SendPushResult

        assert SendPushResult.EXPIRED is not None

    def test_send_push_result_has_failed(self) -> None:
        """SendPushResult must have FAILED member."""
        from claude_remote.services.web_push import SendPushResult

        assert SendPushResult.FAILED is not None


# ---------------------------------------------------------------------------
# send_push — success path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_push_returns_ok_on_success() -> None:
    """send_push returns OK when pywebpush.webpush succeeds. (SC-4.1)"""
    from claude_remote.services.web_push import SendPushResult, send_push

    with patch("claude_remote.services.web_push.webpush") as mock_wp:
        mock_wp.return_value = None  # successful call returns None
        result = await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "Title", "Body")

    assert result == SendPushResult.OK


@pytest.mark.anyio
async def test_send_push_calls_webpush_with_correct_args() -> None:
    """send_push passes subscription_info, a Vapid object, vapid_claims to webpush."""
    from claude_remote.services.web_push import _VAPID_CLAIMS, send_push

    with patch("claude_remote.services.web_push.webpush") as mock_wp:
        mock_wp.return_value = None
        await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "Title", "Body", ttl=30)

    mock_wp.assert_called_once()
    kwargs = mock_wp.call_args.kwargs
    assert kwargs["subscription_info"] == _SUBSCRIPTION_INFO
    # pywebpush mishandles a raw PEM string (routes it to Vapid.from_string
    # which b64-decodes the PEM header). We must hand it a Vapid object.
    assert isinstance(kwargs["vapid_private_key"], Vapid)
    assert kwargs["vapid_claims"] == _VAPID_CLAIMS
    assert kwargs["ttl"] == 30


@pytest.mark.anyio
async def test_send_push_passes_vapid_object_built_from_pem() -> None:
    """send_push must convert the stored PEM to a Vapid object before webpush.

    Regression: passing the PEM string straight through makes pywebpush call
    Vapid.from_string(pem), which raises ValueError ("Could not deserialize
    key data") and the push is silently dropped as FAILED.
    """
    from claude_remote.services.web_push import SendPushResult, send_push

    with patch("claude_remote.services.web_push.webpush") as mock_wp:
        mock_wp.return_value = None
        result = await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B")

    assert result == SendPushResult.OK
    vp = mock_wp.call_args.kwargs["vapid_private_key"]
    assert isinstance(vp, Vapid)
    # The Vapid object must be a real, signable key (not a half-built stub).
    header = vp.sign({"sub": "mailto:user@claude-remote.local", "aud": "https://x.example"})
    assert "Authorization" in header


@pytest.mark.anyio
async def test_send_push_does_not_poison_shared_claims() -> None:
    """pywebpush mutates vapid_claims in place (adds aud/exp). send_push must
    pass a per-call copy so the module-level _VAPID_CLAIMS stays pristine,
    otherwise a second device on a different push origin gets a stale aud."""
    from claude_remote.services import web_push

    def _fake_webpush(**kwargs: object) -> None:
        # Simulate pywebpush's in-place mutation of the claims dict.
        claims = kwargs["vapid_claims"]
        assert isinstance(claims, dict)
        claims["aud"] = "https://fcm.googleapis.com"
        claims["exp"] = 1234567890

    with patch("claude_remote.services.web_push.webpush", side_effect=_fake_webpush):
        await web_push.send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B")

    assert web_push._VAPID_CLAIMS == {"sub": "mailto:user@claude-remote.local"}, (
        f"shared claims were mutated: {web_push._VAPID_CLAIMS}"
    )


@pytest.mark.anyio
async def test_send_push_encodes_payload_as_json() -> None:
    """send_push encodes {title, body, data} as JSON in the payload."""
    from claude_remote.services.web_push import send_push

    with patch("claude_remote.services.web_push.webpush") as mock_wp:
        mock_wp.return_value = None
        await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B", data={"url": "/p/1"})

    kwargs = mock_wp.call_args.kwargs
    payload = json.loads(kwargs["data"])
    assert payload["title"] == "T"
    assert payload["body"] == "B"
    assert payload["data"] == {"url": "/p/1"}


# ---------------------------------------------------------------------------
# send_push — EXPIRED (404 / 410)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_push_returns_expired_on_410() -> None:
    """send_push returns EXPIRED when WebPushException carries HTTP 410. (SC-4.2)"""
    from claude_remote.services.web_push import SendPushResult, send_push

    with patch(
        "claude_remote.services.web_push.webpush",
        side_effect=_make_webpush_exc(410),
    ):
        result = await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B")

    assert result == SendPushResult.EXPIRED


@pytest.mark.anyio
async def test_send_push_returns_expired_on_404() -> None:
    """send_push returns EXPIRED when WebPushException carries HTTP 404. (SC-4.3)"""
    from claude_remote.services.web_push import SendPushResult, send_push

    with patch(
        "claude_remote.services.web_push.webpush",
        side_effect=_make_webpush_exc(404),
    ):
        result = await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B")

    assert result == SendPushResult.EXPIRED


# ---------------------------------------------------------------------------
# send_push — FAILED paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_push_returns_failed_on_network_error() -> None:
    """send_push returns FAILED when a generic exception is raised. (SC-4.4)"""
    from claude_remote.services.web_push import SendPushResult, send_push

    with patch(
        "claude_remote.services.web_push.webpush",
        side_effect=ConnectionError("network down"),
    ):
        result = await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B")

    assert result == SendPushResult.FAILED


@pytest.mark.anyio
async def test_send_push_returns_failed_on_5xx() -> None:
    """send_push returns FAILED when WebPushException carries HTTP 500. (SC-4.5)"""
    from claude_remote.services.web_push import SendPushResult, send_push

    with patch(
        "claude_remote.services.web_push.webpush",
        side_effect=_make_webpush_exc(500),
    ):
        result = await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B")

    assert result == SendPushResult.FAILED


@pytest.mark.anyio
async def test_send_push_returns_failed_on_webpush_exc_no_response() -> None:
    """send_push returns FAILED when WebPushException has no .response attribute."""
    from claude_remote.services.web_push import SendPushResult, send_push

    exc = WebPushException("no response info")
    # No .response attribute — getattr(..., None) fallback must handle this
    with patch("claude_remote.services.web_push.webpush", side_effect=exc):
        result = await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", "B")

    assert result == SendPushResult.FAILED


# ---------------------------------------------------------------------------
# send_push — body cap
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_push_caps_body_at_1000_chars() -> None:
    """Payload body must be capped at 1000 characters before JSON encoding. (SC-4.6)"""
    from claude_remote.services.web_push import send_push

    long_body = "x" * 2000
    with patch("claude_remote.services.web_push.webpush") as mock_wp:
        mock_wp.return_value = None
        await send_push(_SUBSCRIPTION_INFO, _PRIVATE_PEM, "T", long_body)

    kwargs = mock_wp.call_args.kwargs
    payload = json.loads(kwargs["data"])
    assert len(payload["body"]) <= 1000
