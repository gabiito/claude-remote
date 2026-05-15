"""Web Push egress service — VAPID-authenticated push notifications.

Spec: REQ-4 (SC-4.1–4.12)
ADR-4: pywebpush sync API wrapped in asyncio.to_thread to avoid blocking the event loop.
ADR-13: test mock seam is 'claude_remote.services.web_push.webpush' (module-level import).
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import Any

from py_vapid import Vapid  # type: ignore[import-untyped]
from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

from claude_remote.db.push_subscriptions import (
    PushSubscription,
    PushSubscriptionsRepository,
)
from claude_remote.db.vapid_keys import VapidKeysRepository

logger = logging.getLogger(__name__)

# VAPID contact claim — required by spec. Single-user local app; value is never delivered.
_VAPID_CLAIMS: dict[str, str | int] = {"sub": "mailto:user@claude-remote.local"}


class SendPushResult(Enum):
    """Result of a single push delivery attempt."""

    OK = "ok"
    EXPIRED = "expired"
    FAILED = "failed"


def _sync_send_push(
    subscription_info: dict[str, Any],
    vapid_private_pem: str,
    title: str,
    body: str,
    data: dict[str, Any] | None,
    ttl: int,
) -> SendPushResult:
    """Synchronous push via pywebpush. Never raises.

    Called via asyncio.to_thread to avoid blocking the event loop.
    """
    payload = json.dumps({"title": title, "body": body[:1000], "data": data or {}})
    try:
        # pywebpush routes a raw PEM string to Vapid.from_string(), which
        # b64-decodes the PEM (header included) and raises ValueError. Build
        # the Vapid object explicitly via from_pem so the key actually loads.
        vapid_key = Vapid.from_pem(vapid_private_pem.encode("ascii"))
        # pywebpush mutates vapid_claims in place (adds aud/exp). Pass a fresh
        # copy each call so the module-level singleton stays pristine across
        # devices on different push origins.
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=vapid_key,
            vapid_claims=dict(_VAPID_CLAIMS),
            ttl=ttl,
        )
        return SendPushResult.OK
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return SendPushResult.EXPIRED
        logger.warning(
            "web_push send failed (status=%s) for endpoint=%s: %s",
            status,
            str(subscription_info.get("endpoint", "?"))[:60],
            exc,
        )
        return SendPushResult.FAILED
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "web_push unexpected error for endpoint=%s: %s",
            str(subscription_info.get("endpoint", "?"))[:60],
            exc,
        )
        return SendPushResult.FAILED


async def send_push(
    subscription_info: dict[str, Any],
    vapid_private_pem: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    ttl: int = 60,
) -> SendPushResult:
    """Async wrapper over _sync_send_push. Runs in thread pool to avoid loop blocking.

    Never raises. Returns SendPushResult.

    Spec: REQ-4.3, SC-4.1–4.6
    """
    return await asyncio.to_thread(
        _sync_send_push,
        subscription_info,
        vapid_private_pem,
        title,
        body,
        data,
        ttl,
    )


async def send_to_all(
    subscriptions_repo: PushSubscriptionsRepository,
    vapid_repo: VapidKeysRepository,
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> list[SendPushResult]:
    """Fan-out push to all subscriptions. Cleans up EXPIRED rows. Never raises.

    Uses asyncio.gather for parallel per-device delivery. Since send_push never
    raises, gather with return_exceptions=False is safe.

    Spec: REQ-4.9–4.12, SC-4.7–4.9
    """
    try:
        subs = subscriptions_repo.list_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_to_all: list_all failed: %s", exc)
        return []

    if not subs:
        return []

    try:
        vapid = vapid_repo.get()
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_to_all: vapid get failed: %s", exc)
        return []

    async def _one(sub: PushSubscription) -> tuple[PushSubscription, SendPushResult]:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        result = await send_push(
            subscription_info,
            vapid.private_key,
            title,
            body,
            data=data,
        )
        return sub, result

    pairs: list[tuple[PushSubscription, SendPushResult]] = await asyncio.gather(
        *(_one(s) for s in subs), return_exceptions=False
    )

    results: list[SendPushResult] = []
    for sub, result in pairs:
        results.append(result)
        if result is SendPushResult.EXPIRED:
            try:
                subscriptions_repo.delete_by_endpoint(sub.endpoint)
            except Exception as exc:  # noqa: BLE001
                logger.warning("send_to_all: delete_by_endpoint failed: %s", exc)

    return results
