from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from urllib.error import HTTPError
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from app.schemas.bokamera import (
    BokaMeraCustomer,
    CreateBokaMeraBookingRequest,
)
from app.services.bokamera_client import BokaMeraClient, BokaMeraClientError


COMPANY_ID = UUID("00000000-0000-4000-8000-000000000001")
API_KEY = "super-secret-bokamera-key"


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def _client() -> BokaMeraClient:
    return BokaMeraClient(
        api_key=SecretStr(API_KEY),
        company_id=COMPANY_ID,
    )


def test_lists_active_services_with_tenant_scoped_query(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(
            {
                "Results": [
                    {
                        "Id": 12,
                        "Name": "Hjulskifte",
                        "Description": "Byte av fyra hjul",
                        "LengthInMinutes": 30,
                        "Active": True,
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "app.services.bokamera_client.urllib_request.urlopen",
        fake_urlopen,
    )

    services = _client().list_active_services()

    assert services[0].name == "Hjulskifte"
    assert services[0].duration_minutes == 30
    request = captured["request"]
    assert "CompanyId=00000000-0000-4000-8000-000000000001" in request.full_url
    assert "Active=true" in request.full_url
    assert request.get_header("X-api-key") == API_KEY


def test_available_times_keeps_only_slots_with_capacity(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.bokamera_client.urllib_request.urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "Times": [
                    {
                        "From": "2026-09-07T09:00:00+02:00",
                        "To": "2026-09-07T09:30:00+02:00",
                        "FreeSpots": 1,
                    },
                    {
                        "From": "2026-09-07T10:00:00+02:00",
                        "To": "2026-09-07T10:30:00+02:00",
                        "FreeSpots": 0,
                    },
                ]
            }
        ),
    )

    slots = _client().list_available_times(
        service_id=12,
        starts_at=datetime.fromisoformat("2026-09-07T00:00:00+02:00"),
        ends_at=datetime.fromisoformat("2026-09-08T00:00:00+02:00"),
    )

    assert len(slots) == 1
    assert slots[0].starts_at.hour == 9
    assert slots[0].free_spots == 1


def test_create_booking_uses_official_shape_and_never_books_outside_schedule(
    monkeypatch,
) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["method"] = request.method
        captured["body"] = json.loads(request.data)
        return _Response(
            {
                "Id": 419,
                "From": "2026-09-07T09:00:00+02:00",
                "To": "2026-09-07T09:30:00+02:00",
                "StatusName": "Booked",
                "Service": {"Id": 12},
            }
        )

    monkeypatch.setattr(
        "app.services.bokamera_client.urllib_request.urlopen",
        fake_urlopen,
    )
    request = CreateBokaMeraBookingRequest(
        service_id=12,
        starts_at=datetime.fromisoformat("2026-09-07T09:00:00+02:00"),
        ends_at=datetime.fromisoformat("2026-09-07T09:30:00+02:00"),
        customer=BokaMeraCustomer(
            first_name="Anna",
            last_name="Andersson",
            phone="+46701234567",
            email="anna@example.com",
        ),
        booked_comments="Registreringsnummer ABC123. Kontrollera bromsar.",
    )

    booking = _client().create_booking(request)

    assert booking.booking_id == 419
    assert captured["method"] == "POST"
    assert captured["body"]["ServiceId"] == 12
    assert captured["body"]["Customer"]["Phone"] == "+46701234567"
    assert captured["body"]["AllowBookingOutsideSchedules"] is False


def test_email_is_required_only_when_email_confirmation_is_enabled() -> None:
    customer = BokaMeraCustomer(
        first_name="Anna",
        last_name="Andersson",
        phone="+46701234567",
    )
    with pytest.raises(ValidationError):
        CreateBokaMeraBookingRequest(
            service_id=12,
            starts_at=datetime.fromisoformat("2026-09-07T09:00:00+02:00"),
            ends_at=datetime.fromisoformat("2026-09-07T09:30:00+02:00"),
            customer=customer,
        )

    request = CreateBokaMeraBookingRequest(
        service_id=12,
        starts_at=datetime.fromisoformat("2026-09-07T09:00:00+02:00"),
        ends_at=datetime.fromisoformat("2026-09-07T09:30:00+02:00"),
        customer=customer,
        send_email_confirmation=False,
    )
    assert request.customer.email is None


def test_http_error_does_not_expose_api_key(monkeypatch) -> None:
    def fake_urlopen(*_args, **_kwargs):
        raise HTTPError(
            url="https://api.bokamera.se/services",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(
                json.dumps(
                    {"ResponseStatus": {"Message": "Unauthorized"}}
                ).encode()
            ),
        )

    monkeypatch.setattr(
        "app.services.bokamera_client.urllib_request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(BokaMeraClientError) as captured:
        _client().list_active_services()

    assert captured.value.status_code == 401
    assert API_KEY not in str(captured.value)
