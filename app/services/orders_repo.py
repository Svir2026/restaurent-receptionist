from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.services.supabase_client import (
    orders_insert,
    orders_select_all,
    orders_select_by_order_id,
    orders_select_by_phone_suffix,
    orders_update,
)
from app.utils.phone import digits_only, phone_suffix_match

logger = logging.getLogger(__name__)

ORDERS_COLUMNS: list[str] = [
    "order_id",
    "customer_name",
    "customer_phone",
    "order_status",
    "created_at",
    "order_type",
    "order_items",
    "party_size",
    "dine_in_time",
    "pickup_time",
    "total",
    "notes",
    "source",
    "cancellation_reason",
]

MATCH_SUFFIX_LEN = 10


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _format_order_items(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for it in items or []:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        qty = it.get("quantity")
        try:
            qty_i = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty_i = 1

        size = (it.get("size") or "").strip()
        size_part = f" ({size})" if size else ""

        price = it.get("price")
        price_part = ""
        if price is not None:
            try:
                price_f = float(price)
                price_part = f" — {price_f:g} kr each"
            except (TypeError, ValueError):
                price_part = ""

        lines.append(f"{qty_i}x {name}{size_part}{price_part}")
    return "\n".join(lines)


def _compute_total(items: list[dict[str, Any]]) -> float:
    total = 0.0
    for it in items or []:
        qty = it.get("quantity")
        price = it.get("price")
        try:
            qty_i = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty_i = 1
        try:
            price_f = float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            price_f = 0.0
        total += max(qty_i, 0) * max(price_f, 0.0)
    return round(total, 2)


def _total_for_db(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_order_items(order: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = order.get("order_items", []) or []
    if isinstance(raw_items, str):
        try:
            parsed = json.loads(raw_items)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    if isinstance(raw_items, list):
        return raw_items
    return []


def _prepare_db_row(order: dict[str, Any]) -> dict[str, Any]:
    """Prepare an order dict for insertion into Supabase."""
    items = _coerce_order_items(order)
    item_notes: list[str] = []
    for it in items:
        n = (it.get("notes") or "").strip()
        if n:
            item_name = (it.get("name") or "").strip() or "item"
            item_notes.append(f"{item_name}: {n}")
        it["notes"] = None

    base_notes = (order.get("notes") or "").strip()
    combined_notes = base_notes
    if item_notes:
        suffix = "Item notes: " + "; ".join(item_notes)
        combined_notes = f"{base_notes} | {suffix}".strip(" |") if base_notes else suffix

    total = order.get("total")
    if total is None:
        total = _compute_total(items)

    return {
        "order_id": _safe_str(order.get("order_id")),
        "customer_name": _safe_str(order.get("customer_name")),
        "customer_phone": _safe_str(order.get("customer_phone")),
        "order_status": _safe_str(order.get("order_status")),
        "created_at": _safe_str(order.get("created_at")),
        "order_type": _safe_str(order.get("order_type")),
        "order_items": items,
        "party_size": _safe_str(order.get("party_size")),
        "dine_in_time": _safe_str(order.get("dine_in_time")),
        "pickup_time": _safe_str(order.get("pickup_time")),
        "total": _total_for_db(total),
        "notes": combined_notes,
        "source": _safe_str(order.get("source")),
        "cancellation_reason": "",
    }


def _updates_for_supabase(updates: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in updates.items():
        if k == "order_items" and isinstance(v, list):
            out[k] = v
        elif k == "total":
            out[k] = _total_for_db(v)
        else:
            out[k] = v
    return out


def _normalize_row(row: dict[str, Any]) -> dict[str, str]:
    """Normalize a Supabase row into string values (matching the old OrderRow.data interface)."""
    data: dict[str, str] = {}
    for col in ORDERS_COLUMNS:
        val = row.get(col)
        if val is None:
            data[col] = ""
        elif col == "order_items":
            if isinstance(val, list):
                data[col] = _format_order_items(val)
            elif isinstance(val, str):
                data[col] = val
            else:
                data[col] = str(val)
        elif col == "total":
            if val is None:
                data[col] = ""
            else:
                try:
                    data[col] = f"{float(val):.2f}"
                except (TypeError, ValueError):
                    data[col] = str(val)
        else:
            data[col] = str(val) if val is not None else ""
    return data


@dataclass(frozen=True)
class OrderRow:
    order_id: str
    data: dict[str, str]


class OrdersRepository:

    def append_order(self, order: dict[str, Any]) -> None:
        db_row = _prepare_db_row(order)
        orders_insert(db_row)

    def update_order(self, order_id: str, updates: dict[str, Any]) -> None:
        supabase_patch = _updates_for_supabase(updates)
        if supabase_patch:
            orders_update(order_id, supabase_patch)

    def iter_orders(self) -> list[OrderRow]:
        rows = orders_select_all()
        return [
            OrderRow(
                order_id=(_safe_str(r.get("order_id"))).strip(),
                data=_normalize_row(r),
            )
            for r in rows
        ]

    def find_by_phone(self, phone: str) -> list[OrderRow]:
        phone_digits = digits_only(phone)
        suffix = phone_digits[-MATCH_SUFFIX_LEN:] if len(phone_digits) >= MATCH_SUFFIX_LEN else phone_digits
        if not suffix:
            return []
        rows = orders_select_by_phone_suffix(suffix)
        results = []
        for r in rows:
            if phone_suffix_match(r.get("customer_phone"), phone):
                results.append(
                    OrderRow(
                        order_id=(_safe_str(r.get("order_id"))).strip(),
                        data=_normalize_row(r),
                    )
                )
        return results

    def find_by_order_id(self, order_id: str) -> OrderRow | None:
        row = orders_select_by_order_id(order_id)
        if not row:
            return None
        return OrderRow(
            order_id=(_safe_str(row.get("order_id"))).strip(),
            data=_normalize_row(row),
        )

    def update_status(
        self,
        order_id: str,
        new_status: str,
        *,
        cancellation_reason: str | None = None,
    ) -> None:
        patch: dict[str, Any] = {"order_status": new_status}
        if cancellation_reason is not None:
            patch["cancellation_reason"] = _safe_str(cancellation_reason)
        orders_update(order_id, patch)
