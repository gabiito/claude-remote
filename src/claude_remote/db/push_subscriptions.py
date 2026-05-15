"""PushSubscriptionsRepository — multi-row table keyed by endpoint URL.

Spec: REQ-1 (migration), REQ-2 (model + repo).
list_all() orders by created_at ASC per SC-2.3 (spec wins over design sample which showed DESC).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


class PushSubscription(BaseModel):
    id: int
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None
    created_at: str
    last_seen_at: str | None


ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]


class PushSubscriptionsRepository:
    """CRUD for push_subscriptions. Uses upsert on endpoint UNIQUE key.

    Args:
        connection_factory: no-arg callable returning a context manager that
            yields an open sqlite3.Connection (commit on clean exit, rollback
            on exception).
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._factory = connection_factory

    def create(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None,
    ) -> PushSubscription:
        """Upsert by endpoint. On conflict, refresh keys + ua + last_seen_at.

        created_at is NOT updated on conflict — only set on first INSERT.
        Returns the resulting row (fresh DB read after upsert).

        Spec SC-2.2: created_at preserved on re-subscribe.
        """
        now = datetime.now(UTC).isoformat()
        with self._factory() as conn:
            conn.execute(
                """
                INSERT INTO push_subscriptions
                    (endpoint, p256dh, auth, user_agent, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    p256dh = excluded.p256dh,
                    auth = excluded.auth,
                    user_agent = excluded.user_agent,
                    last_seen_at = excluded.last_seen_at
                """,
                (endpoint, p256dh, auth, user_agent, now, now),
            )
        return self._fetch_by_endpoint(endpoint)

    def list_all(self) -> list[PushSubscription]:
        """Return all rows ordered by created_at ASC (spec SC-2.3).

        Note: design sample showed DESC — spec is authoritative here.
        """
        with self._factory() as conn:
            rows = conn.execute(
                """
                SELECT id, endpoint, p256dh, auth, user_agent, created_at, last_seen_at
                FROM push_subscriptions
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def delete_by_endpoint(self, endpoint: str) -> bool:
        """Delete the row matching endpoint. Returns True if deleted, False if not found."""
        with self._factory() as conn:
            cur = conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            )
            return cur.rowcount > 0

    def _fetch_by_endpoint(self, endpoint: str) -> PushSubscription:
        with self._factory() as conn:
            row = conn.execute(
                """
                SELECT id, endpoint, p256dh, auth, user_agent, created_at, last_seen_at
                FROM push_subscriptions WHERE endpoint = ?
                """,
                (endpoint,),
            ).fetchone()
        if row is None:
            raise RuntimeError(
                f"push_subscriptions row missing after upsert: {endpoint!r}"
            )
        return self._row_to_model(row)

    @staticmethod
    def _row_to_model(row: Any) -> PushSubscription:
        return PushSubscription(
            id=row[0],
            endpoint=row[1],
            p256dh=row[2],
            auth=row[3],
            user_agent=row[4],
            created_at=row[5],
            last_seen_at=row[6],
        )
