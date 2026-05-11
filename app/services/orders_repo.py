from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.config import settings
from app.services.sheets_client import SheetsClient, get_sheets_client
from app.utils.phone import try_normalize_phone

logger = logging.getLogger(__name__)

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
        header = self.read_header()
        if header == ORDERS_HEADERS:
            return
        if not header:
            logger.warning("Sheet header missing; writing default header row")
            self.client.update_values(
                self.spreadsheet_id,
                self._sheet_range("A1"),
                [ORDERS_HEADERS],
            )
            return
        raise RuntimeError(
            f"Sheet header mismatch. Expected {ORDERS_HEADERS} but found {header}. "
            "Update the sheet headers or adjust ORDERS_HEADERS in code."
        )

    def append_order(self, order: dict[str, Any]) -> None:
        self.ensure_header()
        raw_items = order.get("order_items", []) or []
        items: list[dict[str, Any]]
        if isinstance(raw_items, str):
            try:
                parsed = json.loads(raw_items)
                items = parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                items = []
        elif isinstance(raw_items, list):
            items = raw_items
        else:
            items = []

        # Notes belong in the explicit notes column; keep the items column clean.
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

        row = [
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
        self.client.append_values(self.spreadsheet_id, self._sheet_range("A:N"), [row])

    def update_order(self, row_number: int, updates: dict[str, Any]) -> None:
        self.ensure_header()
        for header, value in updates.items():
            if header not in ORDERS_HEADERS or header in {"order_id", "customer_phone", "created_at", "cancellation_reason"}:
                continue
            column = _column_letter(ORDERS_HEADERS.index(header))
            self.client.update_values(
                self.spreadsheet_id,
                self._sheet_range(f"{column}{row_number}"),
                [[_serialize_cell(header, value)]],
            )

    def iter_orders(self) -> Iterable[OrderRow]:
        self.ensure_header()
        values = self.client.get_values(self.spreadsheet_id, self._sheet_range("A2:N"))
        # Row numbers: header is row 1, first data row is row 2
        for idx, row in enumerate(values, start=2):
            # Pad row to header length
            padded = list(row) + [""] * (len(ORDERS_HEADERS) - len(row))
            data = {ORDERS_HEADERS[i]: (padded[i] if i < len(padded) else "") for i in range(len(ORDERS_HEADERS))}
            yield OrderRow(row_number=idx, data=data)

    def find_by_phone(self, phone_e164: str) -> list[OrderRow]:
        return [
            r
            for r in self.iter_orders()
            if try_normalize_phone(r.data.get("customer_phone")) == phone_e164
        ]

    def find_by_order_id(self, order_id: str) -> OrderRow | None:
        for r in self.iter_orders():
            if (r.data.get("order_id") or "").strip() == order_id:
                return r
        return None

    def update_status(
        self,
        row_number: int,
        new_status: str,
        *,
        cancellation_reason: str | None = None,
        strike_through: bool = False,
    ) -> None:
        status_col = _column_letter(ORDERS_HEADERS.index("order_status"))
        self.client.update_values(self.spreadsheet_id, self._sheet_range(f"{status_col}{row_number}"), [[new_status]])

        if cancellation_reason is not None:
            reason_col = _column_letter(ORDERS_HEADERS.index("cancellation_reason"))
            self.client.update_values(
                self.spreadsheet_id,
                self._sheet_range(f"{reason_col}{row_number}"),
                [[_safe_str(cancellation_reason)]],
            )

        if strike_through:
            self.client.set_row_strikethrough(
                spreadsheet_id=self.spreadsheet_id,
                sheet_title=self.tab,
                row_number=row_number,
                start_col=0,
                end_col=len(ORDERS_HEADERS),
                strikethrough=True,
            )
