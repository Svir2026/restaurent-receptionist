from __future__ import annotations

from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from app.core.config import settings


@lru_cache(maxsize=1)
def get_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# ─── Orders ───────────────────────────────────────────────────────────────────


def orders_insert(row: dict[str, Any]) -> None:
    get_client().table(settings.supabase_orders_table).insert(row).execute()


def orders_update(order_id: str, patch: dict[str, Any]) -> None:
    oid = (order_id or "").strip()
    if not oid or not patch:
        return
    (
        get_client()
        .table(settings.supabase_orders_table)
        .update(patch)
        .eq("order_id", oid)
        .execute()
    )


def orders_select_all() -> list[dict[str, Any]]:
    resp = (
        get_client()
        .table(settings.supabase_orders_table)
        .select("*")
        .execute()
    )
    return resp.data or []


def orders_select_by_order_id(order_id: str) -> dict[str, Any] | None:
    oid = (order_id or "").strip()
    if not oid:
        return None
    resp = (
        get_client()
        .table(settings.supabase_orders_table)
        .select("*")
        .eq("order_id", oid)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def orders_select_by_phone_suffix(phone_digits_suffix: str) -> list[dict[str, Any]]:
    """Fetch orders whose customer_phone ends with the given digit suffix.

    Uses PostgreSQL LIKE for server-side filtering.
    """
    suffix = (phone_digits_suffix or "").strip()
    if not suffix:
        return []
    resp = (
        get_client()
        .table(settings.supabase_orders_table)
        .select("*")
        .like("customer_phone", f"%{suffix}")
        .execute()
    )
    return resp.data or []


# ─── Call Logs ────────────────────────────────────────────────────────────────


def call_logs_insert(row: dict[str, Any]) -> None:
    get_client().table(settings.supabase_logs_table).insert(row).execute()
