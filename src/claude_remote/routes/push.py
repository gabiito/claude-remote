"""Push notification API endpoints — /api/push/*.

Spec: REQ-5 (SC-5.1–5.10)
Auth: none — single-user MVP fronted by Tailscale (same posture as rest of API).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.push_subscriptions import PushSubscriptionsRepository
from claude_remote.db.vapid_keys import VapidKeysRepository

router = APIRouter(prefix="/api/push", tags=["push"])


# ---------------------------------------------------------------------------
# DI providers
# ---------------------------------------------------------------------------


def get_push_subscriptions_repo(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> PushSubscriptionsRepository:
    return PushSubscriptionsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


def get_vapid_keys_repo(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> VapidKeysRepository:
    return VapidKeysRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class _SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeBody(BaseModel):
    endpoint: str
    keys: _SubscriptionKeys


class UnsubscribeBody(BaseModel):
    endpoint: str


# ---------------------------------------------------------------------------
# Subscription detail without crypto material (for list response)
# ---------------------------------------------------------------------------


class SubscriptionPublic(BaseModel):
    """Public view of a subscription — no p256dh or auth fields exposed."""

    id: int
    endpoint: str
    user_agent: str | None
    created_at: str
    last_seen_at: str | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/vapid-key")
async def get_vapid_key(
    vapid_repo: VapidKeysRepository = Depends(get_vapid_keys_repo),  # noqa: B008
) -> dict[str, str]:
    """Return the server VAPID public key (URL-safe base64url, no padding).

    Always returns 200 — the keypair is guaranteed to exist after startup. (REQ-5.2, REQ-5.3)
    """
    return {"public_key": vapid_repo.public_key_uncompressed()}


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    body: SubscribeBody,
    request: Request,
    subs_repo: PushSubscriptionsRepository = Depends(get_push_subscriptions_repo),  # noqa: B008
) -> dict[str, object]:
    """Upsert a push subscription by endpoint.

    Captures User-Agent header, truncated to 80 chars server-side. (REQ-5.4, REQ-5.5)
    Returns 201 {subscribed: true} on both new subscribe and re-subscribe. (REQ-5.6)
    """
    raw_ua = (request.headers.get("user-agent") or "").strip() or None
    user_agent = raw_ua[:80] if raw_ua else None

    sub = subs_repo.create(
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=user_agent,
    )
    return {"subscribed": True, "id": sub.id}


@router.post("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeBody,
    subs_repo: PushSubscriptionsRepository = Depends(get_push_subscriptions_repo),  # noqa: B008
) -> dict[str, bool]:
    """Delete a subscription by endpoint.

    Returns 200 {unsubscribed: true} if deleted, {unsubscribed: false} if not found. (REQ-5.7)
    """
    deleted = subs_repo.delete_by_endpoint(body.endpoint)
    return {"unsubscribed": deleted}


@router.get("/subscriptions")
async def list_subscriptions(
    subs_repo: PushSubscriptionsRepository = Depends(get_push_subscriptions_repo),  # noqa: B008
) -> dict[str, list[SubscriptionPublic]]:
    """List all subscriptions, ordered by created_at ASC.

    Does NOT expose p256dh or auth fields. (REQ-5.8, REQ-5.9)
    """
    subs = subs_repo.list_all()
    return {
        "subscriptions": [
            SubscriptionPublic(
                id=s.id,
                endpoint=s.endpoint,
                user_agent=s.user_agent,
                created_at=s.created_at,
                last_seen_at=s.last_seen_at,
            )
            for s in subs
        ]
    }
