from __future__ import annotations

import json
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import UUID


logger = logging.getLogger(__name__)

YZ_RESTAURANT_ID = "fc032c24-1dd6-4f94-9a4e-872a50c2487a"
YZ_SMS_RESTAURANT_NAME = "Thai Wok & Sushi"
YZ_SMS_EXPECTED_SENDER = "YZ THAIWOK"

TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"
TELNYX_TIMEOUT_SECONDS = 5.0

_SWEDISH_MOBILE_E164_PATTERN = re.compile(r"^\+467\d{8}$")
_SAFE_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_TELNYX_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LEADING_MENU_NUMBER_PATTERN = re.compile(r"^\d+\.\s+")
_PROTEIN_VARIANT_PATTERN = re.compile(
    r"^(?P<dish>.+?)\s+[-\u2013\u2014]\s+(?P<variant>[^-\u2013\u2014]+)$"
)
_CUSTOMER_PROTEIN_NAMES = {
    "kyckling": "kyckling",
    "räkor": "räkor",
    "biff": "biff",
    "fläsk": "fläsk",
    "tofu": "tofu",
    "lax": "lax",
    "entrecôte": "entrecôte",
}
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_UNSAFE_NOTE_PATTERN = re.compile(
    r"(?:https?://|\b(?:sek|kronor?|pris|price|total|summa|"
    r"currency|menu_item_id|restaurant_id|conversation_id|"
    r"request_hash|api[_ -]?key|token|debug|internal)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class YZOrderSmsCandidate:
    order_id: str
    recipient: str
    sender: str
    messaging_profile_id: str
    text: str
    api_key: str = field(repr=False)


class TelnyxSmsRequestError(Exception):
    def __init__(self, safe_reason: str) -> None:
        super().__init__(safe_reason)
        self.safe_reason = safe_reason


def normalize_swedish_sms_recipient(
    value: object,
) -> str | None:
    """Normalize only unambiguous Swedish mobile numbers to E.164."""

    if not isinstance(value, str):
        return None

    candidate = value.strip()

    if re.fullmatch(r"\+467\d{8}", candidate):
        normalized = candidate
    elif re.fullmatch(r"467\d{8}", candidate):
        normalized = f"+{candidate}"
    elif re.fullmatch(r"07\d{8}", candidate):
        normalized = f"+46{candidate[1:]}"
    else:
        return None

    if not _SWEDISH_MOBILE_E164_PATTERN.fullmatch(normalized):
        return None

    return normalized


def _feature_enabled() -> bool:
    value = os.environ.get("YZ_ORDER_SMS_ENABLED")
    return isinstance(value, str) and value.strip().lower() == "true"


def _safe_order_id(value: object) -> str | None:
    normalized = str(value or "").strip()
    if not _SAFE_ORDER_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _clean_item_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 200:
        return None
    display_name = _LEADING_MENU_NUMBER_PATTERN.sub("", normalized)
    variant_match = _PROTEIN_VARIANT_PATTERN.fullmatch(display_name)

    if variant_match is not None:
        variant = variant_match.group("variant").strip()
        customer_variant = _CUSTOMER_PROTEIN_NAMES.get(
            variant.casefold()
        )
        if customer_variant is not None:
            dish = variant_match.group("dish").strip()
            display_name = f"{dish} med {customer_variant}"

    return display_name


def _clean_customer_note(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 160:
        return None
    if _UNSAFE_NOTE_PATTERN.search(normalized):
        return None
    if _UUID_PATTERN.search(normalized):
        return None
    return normalized


def build_yz_order_confirmation_text(
    *,
    order_id: object,
    items: object,
) -> str:
    normalized_order_id = _safe_order_id(order_id)
    if normalized_order_id is None:
        raise ValueError("invalid_order_id")

    if not isinstance(items, list) or not items:
        raise ValueError("invalid_items")

    item_texts: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid_item")

        name = _clean_item_name(
            item.get("official_name") or item.get("name")
        )

        try:
            quantity = int(item.get("quantity"))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_item_quantity") from error

        if name is None or quantity < 1 or quantity > 100:
            raise ValueError("invalid_item")

        item_text = f"{quantity}x {name}"
        note = _clean_customer_note(item.get("notes"))
        if note:
            item_text = f"{item_text} och {note}"

        item_texts.append(item_text)

    item_summary = "\n".join(item_texts)
    return (
        f"Tack för din beställning hos {YZ_SMS_RESTAURANT_NAME}.\n\n"
        f"Din order är registrerad:\n\n{item_summary}\n\n"
        "Välkommen!"
    )


def _read_required_config(
    *,
    order_id: str,
) -> tuple[str, str, str] | None:
    api_key = os.environ.get("TELNYX_SMS_API_KEY", "").strip()
    profile_id = os.environ.get(
        "TELNYX_MESSAGING_PROFILE_ID",
        "",
    ).strip()
    sender = os.environ.get("TELNYX_SMS_FROM", "").strip()

    if not api_key:
        _log_failure(order_id, "missing_telnyx_sms_api_key")
        return None

    try:
        UUID(profile_id)
    except (ValueError, AttributeError):
        _log_failure(order_id, "invalid_messaging_profile_id")
        return None

    if sender != YZ_SMS_EXPECTED_SENDER:
        _log_failure(order_id, "invalid_sms_sender")
        return None

    return api_key, profile_id, sender


def prepare_yz_order_confirmation_sms(
    *,
    success: bool,
    idempotent_replay: bool,
    restaurant_id: object,
    order_id: object,
    customer_phone: object,
    items: object,
) -> YZOrderSmsCandidate | None:
    """Prepare, but never send, one eligible YZ confirmation SMS."""

    if (
        not success
        or idempotent_replay
        or str(restaurant_id) != YZ_RESTAURANT_ID
        or not _feature_enabled()
    ):
        return None

    normalized_order_id = _safe_order_id(order_id)
    if normalized_order_id is None:
        _log_failure("unknown", "invalid_order_id")
        return None

    recipient = normalize_swedish_sms_recipient(customer_phone)
    if recipient is None:
        _log_failure(normalized_order_id, "invalid_recipient")
        return None

    config = _read_required_config(order_id=normalized_order_id)
    if config is None:
        return None

    try:
        text = build_yz_order_confirmation_text(
            order_id=normalized_order_id,
            items=items,
        )
    except ValueError as error:
        _log_failure(normalized_order_id, str(error))
        return None

    api_key, profile_id, sender = config
    return YZOrderSmsCandidate(
        order_id=normalized_order_id,
        recipient=recipient,
        sender=sender,
        messaging_profile_id=profile_id,
        text=text,
        api_key=api_key,
    )


def _post_telnyx_message(
    candidate: YZOrderSmsCandidate,
) -> str:
    payload = {
        "from": candidate.sender,
        "to": candidate.recipient,
        "text": candidate.text,
        "messaging_profile_id": candidate.messaging_profile_id,
        "type": "SMS",
    }

    request = urllib_request.Request(
        TELNYX_MESSAGES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {candidate.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(
            request,
            timeout=TELNYX_TIMEOUT_SECONDS,
        ) as response:
            status = response.getcode()
            raw_body = response.read(65_536)
    except urllib_error.HTTPError as error:
        raise TelnyxSmsRequestError(
            f"telnyx_http_{error.code}"
        ) from error
    except (TimeoutError, socket.timeout) as error:
        raise TelnyxSmsRequestError("telnyx_timeout") from error
    except urllib_error.URLError as error:
        raise TelnyxSmsRequestError("telnyx_network_error") from error

    if status < 200 or status >= 300:
        raise TelnyxSmsRequestError(f"telnyx_http_{status}")

    try:
        response_payload: Any = json.loads(raw_body)
        message_id = str(
            response_payload.get("data", {}).get("id", "")
        ).strip()
    except (json.JSONDecodeError, AttributeError, TypeError) as error:
        raise TelnyxSmsRequestError(
            "invalid_telnyx_response"
        ) from error

    if not _SAFE_TELNYX_ID_PATTERN.fullmatch(message_id):
        raise TelnyxSmsRequestError("missing_telnyx_message_id")

    return message_id


def _log_failure(order_id: str, reason: str) -> None:
    logger.warning(
        "YZ_ORDER_SMS_FAILED order_id=%s reason=%s",
        order_id,
        reason,
    )


def send_yz_order_confirmation_sms(
    candidate: YZOrderSmsCandidate,
) -> bool:
    """Best-effort Telnyx side effect that never propagates failures."""

    try:
        message_id = _post_telnyx_message(candidate)
    except TelnyxSmsRequestError as error:
        _log_failure(candidate.order_id, error.safe_reason)
        return False
    except Exception:
        _log_failure(candidate.order_id, "unexpected_sms_error")
        return False

    logger.info(
        "YZ_ORDER_SMS_SENT order_id=%s telnyx_message_id=%s",
        candidate.order_id,
        message_id,
    )
    return True
