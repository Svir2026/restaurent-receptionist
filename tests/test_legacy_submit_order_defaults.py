import os
from datetime import datetime


os.environ.setdefault("RESTAURANT_TIMEZONE", "Europe/Stockholm")
os.environ.setdefault("ELEVENLABS_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault(
    "SVIR_INTERNAL_API_SECRET",
    "test-internal-secret-with-at-least-thirty-two-characters",
)
os.environ.setdefault("ELEVENLABS_API_KEY", "test-api-key")
os.environ.setdefault("ELEVENLABS_TEMPLATE_AGENT_ID", "test-agent")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from app.api.routes import orders
from app.schemas.orders import SubmitOrderRequest


class _FakeOrdersRepository:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append_order(self, row: dict[str, object]) -> None:
        self.rows.append(row)


def test_legacy_submit_order_defaults_to_short_takeaway_flow(monkeypatch) -> None:
    repo = _FakeOrdersRepository()
    now = datetime.fromisoformat("2026-08-31T12:00:00+02:00")
    monkeypatch.setattr(orders, "_orders_repo", lambda: repo)
    monkeypatch.setattr(orders, "tz_now", lambda _timezone: now)

    payload = SubmitOrderRequest.model_validate(
        {
            "customer_phone": "+46701234567",
            "order_items": [{"name": "Kebab Pizza", "quantity": 1, "price": 130}],
            "total": 130,
        }
    )

    response = orders.submit_order(payload)

    assert response.order_status == "new order"
    assert repo.rows[0]["customer_name"] == "Telefonkund"
    assert repo.rows[0]["order_type"] == "takeaway"
    assert repo.rows[0]["pickup_time"] == "2026-08-31 12:15"
    assert repo.rows[0]["dine_in_time"] == ""


def test_legacy_submit_order_preserves_explicit_dine_in_choice(monkeypatch) -> None:
    repo = _FakeOrdersRepository()
    now = datetime.fromisoformat("2026-08-31T12:00:00+02:00")
    monkeypatch.setattr(orders, "_orders_repo", lambda: repo)
    monkeypatch.setattr(orders, "tz_now", lambda _timezone: now)

    payload = SubmitOrderRequest.model_validate(
        {
            "customer_phone": "+46701234567",
            "order_type": "dine_in",
            "order_items": [{"name": "Kebab Pizza", "quantity": 1, "price": 130}],
            "total": 130,
        }
    )

    orders.submit_order(payload)

    assert repo.rows[0]["order_type"] == "dine_in"
    assert repo.rows[0]["dine_in_time"] == "2026-08-31 12:15"
    assert repo.rows[0]["pickup_time"] == ""
