from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import ulid
from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.schemas.orders import (
    CancelOrderRequest,
    CancelOrderResponse,
    CheckOrderStatusResponse,
    CheckOrderStatusResponseItem,
    OrderType,
    SubmitOrderRequest,
    SubmitOrderResponse,
    UpdateOrderRequest,
    UpdateOrderResponse,
)
from app.services.orders_repo import OrdersRepository, OrderRow
from app.utils.phone import normalize_phone, phone_suffix_match
from app.utils.time import coerce_to_tz, make_window, tz_now

router = APIRouter(tags=["orders"])

# Sheet / staff workflow: new order → preparing → ready → completed | cancelled
ACTIVE_STATUSES = {"new order", "preparing", "ready"}
CANCELLABLE_STATUSES = {"new order"}
# Legacy rows (before status rename) remain valid for lookups and cancel until migrated
LEGACY_ACTIVE_STATUSES = {"submitted", "confirmed"}
ACTIVE_STATUSES |= LEGACY_ACTIVE_STATUSES
CANCELLABLE_STATUSES |= LEGACY_ACTIVE_STATUSES


def _require_phone_or_422(value: str | None, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise HTTPException(status_code=422, detail=f"{field_name} is required")
    return str(value)


def _resolve_caller_phone(raw: str | None, field_name: str) -> str:
    try:
        return normalize_phone(_require_phone_or_422(raw, field_name))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def _parse_dt(value: str) -> datetime | None:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _fmt_sheet_dt(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def _row_relevant_time(row: OrderRow) -> datetime | None:
    order_type = (row.data.get("order_type") or "").strip()
    if order_type == "takeaway":
        return _parse_dt(row.data.get("pickup_time") or "")
    if order_type == "dine_in":
        return _parse_dt(row.data.get("dine_in_time") or "") or _parse_dt(row.data.get("created_at") or "")
    return _parse_dt(row.data.get("created_at") or "")


def _row_created_at(row: OrderRow) -> datetime | None:
    return _parse_dt(row.data.get("created_at") or "")


def _row_sort_comparator(row: OrderRow) -> datetime | None:
    relevant = _row_relevant_time(row)
    created = _row_created_at(row) or relevant
    if not created:
        return None
    relevant_tz = coerce_to_tz(relevant, settings.restaurant_timezone) if relevant else None
    created_tz = coerce_to_tz(created, settings.restaurant_timezone)
    return relevant_tz or created_tz


def _row_order_type(row: OrderRow) -> OrderType:
    order_type = (row.data.get("order_type") or "").strip()
    return "dine_in" if order_type == "dine_in" else "takeaway"


def _row_total_optional(row: OrderRow) -> float | None:
    t = (row.data.get("total") or "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _orders_repo() -> OrdersRepository:
    return OrdersRepository.from_settings()


def _lookahead_window() -> tuple[datetime, datetime, datetime]:
    now = tz_now(settings.restaurant_timezone)
    window_start, window_end = make_window(now, settings.lookahead_hours)
    return now, window_start, window_end


def _active_status_ok(status: str) -> bool:
    return not (status and status not in ACTIVE_STATUSES)


def _cancellable_status_ok(status: str) -> bool:
    return status in CANCELLABLE_STATUSES


def _collect_window_candidates(
    rows: list[OrderRow],
    window_start: datetime,
    window_end: datetime,
    *,
    status_ok: Callable[[str], bool],
) -> list[tuple[datetime, OrderRow]]:
    out: list[tuple[datetime, OrderRow]] = []
    for row in rows:
        status = (row.data.get("order_status") or "").strip().lower()
        if not status_ok(status):
            continue
        comparator = _row_sort_comparator(row)
        if comparator is None or comparator < window_start or comparator > window_end:
            continue
        out.append((comparator, row))
    return out


def _latest_by_comparator(candidates: list[tuple[datetime, OrderRow]]) -> OrderRow | None:
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _active_status_or_error(row: OrderRow) -> str:
    status = (row.data.get("order_status") or "").strip().lower()
    if status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"order is not updatable; current status is {status or 'unknown'}",
        )
    return status


def _cancellable_status_or_error(row: OrderRow) -> str:
    status = (row.data.get("order_status") or "").strip().lower()
    if status not in CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"order is not cancellable; current status is {status or 'unknown'}",
        )
    return status


def _latest_active_order_in_window(
    rows: list[OrderRow],
    window_start: datetime,
    window_end: datetime,
) -> OrderRow | None:
    candidates = _collect_window_candidates(
        rows,
        window_start,
        window_end,
        status_ok=_active_status_ok,
    )
    return _latest_by_comparator(candidates)


def _fetch_order_for_mutation(
    repo: OrdersRepository,
    *,
    order_id: str | None,
    phone: str,
    window_start: datetime,
    window_end: datetime,
) -> OrderRow | None:
    if order_id:
        return repo.find_by_order_id(order_id)
    return _latest_active_order_in_window(repo.find_by_phone(phone), window_start, window_end)


def _assert_order_belongs_to_caller(row: OrderRow, phone: str) -> None:
    if not phone:
        return
    if not phone_suffix_match(row.data.get("customer_phone"), phone):
        raise HTTPException(status_code=403, detail="order_id does not belong to caller_number")


@router.post(
    "/submit-order",
    response_model=SubmitOrderResponse,
)
def submit_order(payload: SubmitOrderRequest) -> SubmitOrderResponse:
    phone = _resolve_caller_phone(payload.customer_phone, "customer_phone")

    now = tz_now(settings.restaurant_timezone)
    order_id = str(ulid.new())

    dine_in_time = (
        coerce_to_tz(payload.dine_in_time, settings.restaurant_timezone) if payload.dine_in_time else None
    )
    pickup_time = (
        coerce_to_tz(payload.pickup_time, settings.restaurant_timezone) if payload.pickup_time else None
    )

    order_status = "new order"
    items = [i.model_dump() for i in payload.order_items]
    total = float(payload.total) if payload.total is not None else None
    order_row = {
        "order_id": order_id,
        "customer_name": payload.customer_name or "",
        "customer_phone": phone,
        "order_status": order_status,
        "created_at": now.isoformat(),
        "order_type": payload.order_type,
        "order_items": items,
        "party_size": payload.party_size,
        "dine_in_time": _fmt_sheet_dt(dine_in_time),
        "pickup_time": _fmt_sheet_dt(pickup_time),
        "total": total,
        "notes": payload.notes or "",
        "source": payload.source or "elevenlabs",
    }

    repo = _orders_repo()
    repo.append_order(order_row)

    return SubmitOrderResponse(order_id=order_id, order_status=order_status, created_at=now, total=total)


@router.get(
    "/check-order-status",
    response_model=CheckOrderStatusResponse,
)
def check_order_status(
    param_caller_number: str | None = Query(None, min_length=3),
) -> CheckOrderStatusResponse:
    phone = _resolve_caller_phone(param_caller_number, "param_caller_number")
    _, window_start, window_end = _lookahead_window()

    repo = _orders_repo()
    rows = repo.find_by_phone(phone)

    results: list[CheckOrderStatusResponseItem] = []
    for r in rows:
        comparator = _row_sort_comparator(r)
        if comparator is None or comparator < window_start or comparator > window_end:
            continue

        relevant = _row_relevant_time(r)
        created = _row_created_at(r) or relevant
        relevant_tz = coerce_to_tz(relevant, settings.restaurant_timezone) if relevant else None
        created_tz = coerce_to_tz(created, settings.restaurant_timezone)

        results.append(
            CheckOrderStatusResponseItem(
                order_id=(r.data.get("order_id") or "").strip(),
                order_status=(r.data.get("order_status") or "").strip(),
                order_type=_row_order_type(r),
                scheduled_time=relevant_tz,
                created_at=created_tz,
                total=_row_total_optional(r),
            )
        )

    results.sort(key=lambda x: (x.scheduled_time or x.created_at), reverse=True)

    return CheckOrderStatusResponse(
        caller_number=phone,
        timezone=settings.restaurant_timezone,
        window_start=window_start,
        window_end=window_end,
        orders=results,
    )


@router.post(
    "/update-order",
    response_model=UpdateOrderResponse,
)
def update_order(payload: UpdateOrderRequest) -> UpdateOrderResponse:
    phone = _resolve_caller_phone(payload.caller_number, "caller_number")
    _, window_start, window_end = _lookahead_window()

    repo = _orders_repo()
    order_id = payload.order_id.strip() if payload.order_id else None
    row = _fetch_order_for_mutation(
        repo,
        order_id=order_id,
        phone=phone,
        window_start=window_start,
        window_end=window_end,
    )

    if order_id:
        if not row:
            raise HTTPException(status_code=404, detail="order_id not found")
        _assert_order_belongs_to_caller(row, phone)
    else:
        if not row:
            return UpdateOrderResponse(updated=False, updated_fields=[])

    _active_status_or_error(row)
    order_status = (row.data.get("order_status") or "").strip()
    fields_set = payload.model_fields_set

    final_order_type = payload.order_type if "order_type" in fields_set else _row_order_type(row)
    final_party_size = payload.party_size if "party_size" in fields_set else (row.data.get("party_size") or "")
    final_pickup_time = payload.pickup_time if "pickup_time" in fields_set else _parse_dt(row.data.get("pickup_time") or "")

    if final_order_type == "dine_in" and not final_party_size:
        raise HTTPException(status_code=422, detail="party_size is required for dine_in orders")
    if final_order_type == "takeaway" and not final_pickup_time:
        raise HTTPException(status_code=422, detail="pickup_time is required for takeaway orders")

    updates: dict[str, object] = {}
    if "customer_name" in fields_set:
        updates["customer_name"] = payload.customer_name or ""
    if "order_type" in fields_set:
        updates["order_type"] = payload.order_type
    if "order_items" in fields_set:
        items = [item.model_dump() for item in (payload.order_items or [])]
        updates["order_items"] = items
        updates["total"] = float(payload.total) if payload.total is not None else None
    if "total" in fields_set and "order_items" not in fields_set:
        updates["total"] = float(payload.total) if payload.total is not None else None
    if "party_size" in fields_set:
        updates["party_size"] = payload.party_size
    if "dine_in_time" in fields_set:
        dine_in_time = (
            coerce_to_tz(payload.dine_in_time, settings.restaurant_timezone) if payload.dine_in_time else None
        )
        updates["dine_in_time"] = _fmt_sheet_dt(dine_in_time)
    if "pickup_time" in fields_set:
        pickup_time = coerce_to_tz(payload.pickup_time, settings.restaurant_timezone) if payload.pickup_time else None
        updates["pickup_time"] = _fmt_sheet_dt(pickup_time)
    if "notes" in fields_set:
        updates["notes"] = payload.notes or ""

    if final_order_type == "dine_in":
        updates["pickup_time"] = ""
    if final_order_type == "takeaway":
        updates["dine_in_time"] = ""

    repo.update_order(
        row.row_number,
        (row.data.get("order_id") or "").strip(),
        updates,
    )
    updated_fields = list(updates.keys())
    total_out: float | None
    if "total" in updates:
        t = updates.get("total")
        total_out = float(t) if t is not None else None
    else:
        total_out = _row_total_optional(row)

    return UpdateOrderResponse(
        updated=True,
        order_id=(row.data.get("order_id") or "").strip(),
        order_status=order_status,
        row_number=row.row_number,
        updated_fields=updated_fields,
        total=total_out,
    )


@router.post(
    "/cancel-order",
    response_model=CancelOrderResponse,
)
def cancel_order(payload: CancelOrderRequest) -> CancelOrderResponse:
    phone = _resolve_caller_phone(payload.caller_number, "caller_number")
    _, window_start, window_end = _lookahead_window()

    repo = _orders_repo()
    cancelled: list[dict[str, str]] = []

    if payload.order_id and payload.order_id.strip():
        oid = payload.order_id.strip()
        row = repo.find_by_order_id(oid)
        if not row:
            raise HTTPException(status_code=404, detail="order_id not found")
        _assert_order_belongs_to_caller(row, phone)
        _cancellable_status_or_error(row)
        repo.update_status(
            row.row_number,
            oid,
            "cancelled",
            cancellation_reason=(payload.reason or ""),
            strike_through=True,
        )
        cancelled.append({"order_id": oid, "row_number": str(row.row_number)})
        return CancelOrderResponse(cancelled=True, cancelled_orders=cancelled)

    rows = repo.find_by_phone(phone)
    candidates = _collect_window_candidates(
        rows,
        window_start,
        window_end,
        status_ok=_cancellable_status_ok,
    )
    chosen = _latest_by_comparator(candidates)
    if not chosen:
        return CancelOrderResponse(cancelled=False, cancelled_orders=[])

    repo.update_status(
        chosen.row_number,
        (chosen.data.get("order_id") or "").strip(),
        "cancelled",
        cancellation_reason=(payload.reason or ""),
        strike_through=True,
    )
    cancelled.append(
        {
            "order_id": (chosen.data.get("order_id") or "").strip(),
            "row_number": str(chosen.row_number),
        }
    )
    return CancelOrderResponse(cancelled=True, cancelled_orders=cancelled)

