from __future__ import annotations

from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from app.core.config import settings


@lru_cache(maxsize=1)
def _client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def orders_insert(row: dict[str, Any]) -> None:
    _client().table(settings.supabase_orders_table).insert(row).execute()


def orders_update(order_id: str, patch: dict[str, Any]) -> None:
    oid = (order_id or "").strip()
    if not oid or not patch:
        return
    _client().table(settings.supabase_orders_table).update(patch).eq("order_id", oid).execute()


def call_logs_insert(row: dict[str, Any]) -> None:
    _client().table(settings.supabase_logs_table).insert(row).execute()
