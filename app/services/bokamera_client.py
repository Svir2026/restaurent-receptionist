from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlencode
from uuid import UUID

from pydantic import SecretStr

from app.schemas.bokamera import (
    BokaMeraAvailableTime,
    BokaMeraBooking,
    BokaMeraService,
    CreateBokaMeraBookingRequest,
)


BOKAMERA_API_BASE_URL = "https://api.bokamera.se"


class BokaMeraClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _parse_json(raw: bytes, *, operation: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BokaMeraClientError(
            "BOKAMERA_INVALID_RESPONSE",
            f"BokaMera returned invalid data while attempting to {operation}.",
        ) from error

    if not isinstance(payload, dict):
        raise BokaMeraClientError(
            "BOKAMERA_INVALID_RESPONSE",
            f"BokaMera returned invalid data while attempting to {operation}.",
        )
    return payload


def _response_error_message(payload: dict[str, Any]) -> str | None:
    status = payload.get("ResponseStatus") or payload.get("responseStatus")
    if not isinstance(status, dict):
        return None
    value = status.get("Message") or status.get("message")
    return str(value).strip()[:300] if value else None


def _parse_datetime(value: object, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise BokaMeraClientError(
            "BOKAMERA_INVALID_RESPONSE",
            f"BokaMera returned an invalid {field_name}.",
        ) from error
    if parsed.tzinfo is None:
        raise BokaMeraClientError(
            "BOKAMERA_INVALID_RESPONSE",
            f"BokaMera returned {field_name} without a timezone.",
        )
    return parsed


class BokaMeraClient:
    """Small authenticated adapter around the official BokaMera API."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        company_id: UUID,
        timeout_seconds: float = 15,
    ) -> None:
        key = api_key.get_secret_value().strip()
        if not key:
            raise ValueError("BokaMera API key is required")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("BokaMera timeout must be between 0 and 60 seconds")
        self._api_key = key
        self.company_id = company_id
        self.timeout_seconds = timeout_seconds

    def _send(
        self,
        *,
        method: str,
        path: str,
        operation: str,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        query_string = urlencode(query or {})
        url = f"{BOKAMERA_API_BASE_URL}{path}"
        if query_string:
            url += f"?{query_string}"

        headers = {
            "Accept": "application/json",
            "x-api-key": self._api_key,
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        outgoing = urllib_request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib_request.urlopen(
                outgoing,
                timeout=self.timeout_seconds,
            ) as response:
                payload = _parse_json(response.read(), operation=operation)
        except urllib_error.HTTPError as error:
            try:
                payload = _parse_json(error.read(), operation=operation)
                detail = _response_error_message(payload)
            except BokaMeraClientError:
                detail = None
            raise BokaMeraClientError(
                "BOKAMERA_HTTP_ERROR",
                detail or f"BokaMera rejected the request with HTTP {error.code}.",
                status_code=error.code,
            ) from error
        except (urllib_error.URLError, TimeoutError) as error:
            raise BokaMeraClientError(
                "BOKAMERA_UNAVAILABLE",
                f"BokaMera was unavailable while attempting to {operation}.",
                status_code=503,
            ) from error

        response_error = _response_error_message(payload)
        if response_error:
            raise BokaMeraClientError(
                "BOKAMERA_API_ERROR",
                response_error,
                status_code=422,
            )
        return payload

    def list_active_services(self) -> list[BokaMeraService]:
        payload = self._send(
            method="GET",
            path="/services",
            operation="list services",
            query={
                "CompanyId": str(self.company_id),
                "Active": "true",
                "IncludePrices": "true",
                "IncludeResources": "true",
                "IncludeBookingCustomFields": "true",
            },
        )
        results = payload.get("Results") or payload.get("results") or []
        if not isinstance(results, list):
            raise BokaMeraClientError(
                "BOKAMERA_INVALID_RESPONSE",
                "BokaMera returned an invalid service list.",
            )

        services: list[BokaMeraService] = []
        for value in results:
            if not isinstance(value, dict):
                raise BokaMeraClientError(
                    "BOKAMERA_INVALID_RESPONSE",
                    "BokaMera returned an invalid service.",
                )
            services.append(
                BokaMeraService(
                    service_id=value.get("Id") or value.get("id"),
                    name=value.get("Name") or value.get("name"),
                    description=value.get("Description")
                    or value.get("description"),
                    duration_minutes=value.get("Duration")
                    or value.get("duration")
                    or value.get("LengthInMinutes")
                    or value.get("lengthInMinutes"),
                    active=bool(value.get("Active", value.get("active", False))),
                )
            )
        return services

    def list_available_times(
        self,
        *,
        service_id: int,
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[BokaMeraAvailableTime]:
        if service_id <= 0:
            raise ValueError("BokaMera service_id must be positive")
        if starts_at.tzinfo is None or ends_at.tzinfo is None:
            raise ValueError("BokaMera availability interval needs a timezone")
        if ends_at <= starts_at:
            raise ValueError("BokaMera availability end must be after start")

        payload = self._send(
            method="GET",
            path=f"/services/{service_id}/availabletimes",
            operation="list available times",
            query={
                "CompanyId": str(self.company_id),
                "From": starts_at.isoformat(),
                "To": ends_at.isoformat(),
                "NumberOfResources": 1,
                "InsideSearchInterval": "true",
            },
        )
        values = payload.get("Times") or payload.get("times") or []
        if not isinstance(values, list):
            raise BokaMeraClientError(
                "BOKAMERA_INVALID_RESPONSE",
                "BokaMera returned an invalid availability list.",
            )

        slots: list[BokaMeraAvailableTime] = []
        for value in values:
            if not isinstance(value, dict):
                raise BokaMeraClientError(
                    "BOKAMERA_INVALID_RESPONSE",
                    "BokaMera returned an invalid available time.",
                )
            free_spots = int(
                value.get("FreeSpots", value.get("freeSpots", 0)) or 0
            )
            if free_spots < 1:
                continue
            slots.append(
                BokaMeraAvailableTime(
                    starts_at=_parse_datetime(
                        value.get("From") or value.get("from"),
                        field_name="start time",
                    ),
                    ends_at=_parse_datetime(
                        value.get("To") or value.get("to"),
                        field_name="end time",
                    ),
                    free_spots=free_spots,
                )
            )
        return slots

    def create_booking(
        self,
        booking: CreateBokaMeraBookingRequest,
    ) -> BokaMeraBooking:
        customer = booking.customer
        body: dict[str, object] = {
            "CompanyId": str(self.company_id),
            "ServiceId": booking.service_id,
            "From": booking.starts_at.isoformat(),
            "To": booking.ends_at.isoformat(),
            "Customer": {
                "Firstname": customer.first_name,
                "Lastname": customer.last_name,
                "Phone": customer.phone,
                "Email": str(customer.email or ""),
            },
            "BookedComments": booking.booked_comments,
            "Resources": [
                {
                    "ResourceTypeId": value.resource_type_id,
                    "ResourceId": value.resource_id,
                }
                for value in booking.resources
            ],
            "CustomFields": [
                {"Id": value.field_id, "Value": value.value}
                for value in booking.custom_fields
            ],
            "Quantities": [
                {
                    "PriceId": booking.price_id,
                    "Quantity": 1,
                    "OccupiesSpot": True,
                }
            ],
            "SendSmsConfirmation": booking.send_sms_confirmation,
            "SendEmailConfirmation": booking.send_email_confirmation,
            "AllowBookingOutsideSchedules": False,
        }
        payload = self._send(
            method="POST",
            path="/bookings",
            operation="create booking",
            body=body,
        )

        service = payload.get("Service") or payload.get("service") or {}
        if not isinstance(service, dict):
            service = {}
        return BokaMeraBooking(
            booking_id=payload.get("Id") or payload.get("id"),
            service_id=service.get("Id")
            or service.get("id")
            or booking.service_id,
            starts_at=_parse_datetime(
                payload.get("From") or payload.get("from"),
                field_name="booking start time",
            ),
            ends_at=_parse_datetime(
                payload.get("To") or payload.get("to"),
                field_name="booking end time",
            ),
            status=payload.get("StatusName")
            or payload.get("statusName")
            or payload.get("Status")
            or payload.get("status")
            or "Booked",
        )
