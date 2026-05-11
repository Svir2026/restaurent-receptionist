from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.config import settings
from app.services.sheets_client import SheetsClient, get_sheets_client
from app.services.supabase_sync import orders_insert, orders_update
from app.utils.phone import phone_suffix_match

logger = logging.getLogger(__name__)

# Skip re-reading row 1 on every request once verified for this process.
_header_verified: set[tuple[str, str]] = set()

ORDERS_HEADERS: list[str] = [
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


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _column_letter(index: int) -> str:
    return chr(ord("A") + index)


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


def _serialize_cell(header: str, value: Any) -> str:
    if header == "order_items":
        # Store human-friendly order items in the sheet.
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return _format_order_items(value)
        return ""
    if header == "total":
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return ""
    return _safe_str(value)


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


def _prepare_append_order(order: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Single preparation path: formatted sheet row + Supabase row (jsonb items)."""
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

    sheet_row = [
        _safe_str(order.get("order_id")),
        _safe_str(order.get("customer_name")),
        _safe_str(order.get("customer_phone")),
        _safe_str(order.get("order_status")),
        _safe_str(order.get("created_at")),
        _safe_str(order.get("order_type")),
        _format_order_items(items),
        _safe_str(order.get("party_size")),
        _safe_str(order.get("dine_in_time")),
        _safe_str(order.get("pickup_time")),
        _serialize_cell("total", total),
        combined_notes,
        _safe_str(order.get("source")),
        "",
    ]
    db_row = {
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
    return sheet_row, db_row


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


@dataclass(frozen=True)
class OrderRow:
    row_number: int  # 1-indexed in Sheets
    data: dict[str, str]


class OrdersRepository:
    def __init__(self, client: SheetsClient, spreadsheet_id: str, tab: str):
        self.client = client
        self.spreadsheet_id = spreadsheet_id
        self.tab = tab

    @classmethod
    def from_settings(cls) -> "OrdersRepository":
        client = get_sheets_client(settings.google_service_account_json)
        return cls(client=client, spreadsheet_id=settings.google_sheet_id, tab=settings.google_sheet_tab)

    def _sheet_range(self, a1: str) -> str:
        return f"{self.tab}!{a1}"

    def read_header(self) -> list[str]:
        values = self.client.get_values(self.spreadsheet_id, self._sheet_range("1:1"))
        return [c.strip() for c in (values[0] if values else [])]

    def ensure_header(self) -> None:
        key = (self.spreadsheet_id, self.tab)
        if key in _header_verified:
            return
        header = self.read_header()
        if header == ORDERS_HEADERS:
            _header_verified.add(key)
            return
        if not header:
            logger.warning("Sheet header missing; writing default header row")
            self.client.update_values(
                self.spreadsheet_id,
                self._sheet_range("A1"),
                [ORDERS_HEADERS],
            )
            _header_verified.add(key)
            return
        raise RuntimeError(
            f"Sheet header mismatch. Expected {ORDERS_HEADERS} but found {header}. "
            "Update the sheet headers or adjust ORDERS_HEADERS in code."
        )

    def append_order(self, order: dict[str, Any]) -> None:
        self.ensure_header()
        sheet_row, db_row = _prepare_append_order(order)
        self.client.append_values(self.spreadsheet_id, self._sheet_range("A:N"), [sheet_row])
        orders_insert(db_row)

    def update_order(self, row_number: int, order_id: str, updates: dict[str, Any]) -> None:
        self.ensure_header()
        batch: list[tuple[str, list[list[Any]]]] = []
        for header, value in updates.items():
            if header not in ORDERS_HEADERS or header in {"order_id", "customer_phone", "created_at", "cancellation_reason"}:
                continue
            column = _column_letter(ORDERS_HEADERS.index(header))
            batch.append(
                (
                    self._sheet_range(f"{column}{row_number}"),
                    [[_serialize_cell(header, value)]],
                )
            )
        if batch:
            self.client.batch_update_values(self.spreadsheet_id, batch)
        supabase_patch = _updates_for_supabase(updates)
        if supabase_patch:
            orders_update(order_id, supabase_patch)

    def iter_orders(self) -> Iterable[OrderRow]:
        self.ensure_header()
        values = self.client.get_values(self.spreadsheet_id, self._sheet_range("A2:N"))
        # Row numbers: header is row 1, first data row is row 2
        for idx, row in enumerate(values, start=2):
            # Pad row to header length
            padded = list(row) + [""] * (len(ORDERS_HEADERS) - len(row))
            data = {ORDERS_HEADERS[i]: (padded[i] if i < len(padded) else "") for i in range(len(ORDERS_HEADERS))}
            yield OrderRow(row_number=idx, data=data)

    def find_by_phone(self, phone: str) -> list[OrderRow]:
        return [r for r in self.iter_orders() if phone_suffix_match(r.data.get("customer_phone"), phone)]

    def find_by_order_id(self, order_id: str) -> OrderRow | None:
        for r in self.iter_orders():
            if (r.data.get("order_id") or "").strip() == order_id:
                return r
        return None

    def update_status(
        self,
        row_number: int,
        order_id: str,
        new_status: str,
        *,
        cancellation_reason: str | None = None,
        strike_through: bool = False,
    ) -> None:
        status_col = _column_letter(ORDERS_HEADERS.index("order_status"))
        values_batch: list[tuple[str, list[list[Any]]]] = [
            (self._sheet_range(f"{status_col}{row_number}"), [[new_status]])
        ]
        if cancellation_reason is not None:
            reason_col = _column_letter(ORDERS_HEADERS.index("cancellation_reason"))
            values_batch.append(
                (self._sheet_range(f"{reason_col}{row_number}"), [[_safe_str(cancellation_reason)]]),
            )
        self.client.batch_update_values(self.spreadsheet_id, values_batch)

        patch: dict[str, Any] = {"order_status": new_status}
        if cancellation_reason is not None:
            patch["cancellation_reason"] = _safe_str(cancellation_reason)
        orders_update(order_id, patch)

        if strike_through:
            self.client.set_row_strikethrough(
                spreadsheet_id=self.spreadsheet_id,
                sheet_title=self.tab,
                row_number=row_number,
                start_col=0,
                end_col=len(ORDERS_HEADERS),
                strikethrough=True,
            )
