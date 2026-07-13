from __future__ import annotations

from typing import Any


def _parse_quantity(value: Any) -> int:
    try:
        qty = int(value) if value is not None else 1
    except (TypeError, ValueError):
        qty = 1
    return max(qty, 0)


def _parse_price(value: Any) -> float:
    try:
        price = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        price = 0.0
    return max(price, 0.0)


def compute_order_total(items: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Sum quantity × price per line. Returns (total, breakdown)."""
    breakdown: list[dict[str, Any]] = []
    total = 0.0

    for it in items or []:
        name = (it.get("name") or "").strip()
        if not name:
            continue

        quantity = _parse_quantity(it.get("quantity"))
        price = _parse_price(it.get("price"))
        line_total = round(quantity * price, 2)
        total += line_total

        breakdown.append(
            {
                "name": name,
                "quantity": quantity,
                "price": price,
                "line_total": line_total,
            }
        )

    return round(total, 2), breakdown
