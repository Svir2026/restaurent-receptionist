from __future__ import annotations

import hmac
import logging
import os
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
)

from app.core.tool_auth import (
    ToolRestaurantContext,
    require_restaurant_tool_context,
)
from app.schemas.restaurant_tools_v2 import (
    CalculateOrderTotalV2Request,
    CalculateOrderTotalV2Response,
    CancelOrderV2Request,
    CancelOrderV2Response,
    CheckOrderStatusV2Request,
    CheckOrderStatusV2Response,
    SubmitOrderV2Request,
    SubmitOrderV2Response,
    UpdateOrderV2Request,
    UpdateOrderV2Response,
)
from app.services.restaurant_menu_pricing import (
    RestaurantMenuPricingError,
    calculate_restaurant_menu_total,
)
from app.services.restaurant_order_canceller import (
    RestaurantOrderCancellationError,
    cancel_restaurant_order,
)
from app.services.restaurant_order_status import (
    RestaurantOrderStatusError,
    check_restaurant_order_status,
)
from app.services.restaurant_order_submitter import (
    RestaurantOrderSubmissionError,
    submit_restaurant_order,
)
from app.services.telnyx_order_sms import (
    prepare_yz_order_confirmation_sms,
    send_yz_order_confirmation_sms,
)
from app.services.restaurant_order_updater import (
    RestaurantOrderUpdateError,
    update_restaurant_order,
)


router = APIRouter(
    prefix="/v2",
    tags=["restaurant-tools-v2"],
)

logger = logging.getLogger(__name__)



YZ_INITIATION_AGENT_ID = (
    "agent_3701kycttzk2e3babhgdksfcjh9g"
)
YZ_INITIATION_BRANCH_ID = (
    "agtbrch_5501kycttzkmf9ksz96y5mbzpj3f"
)
YZ_INITIATION_PHONE_NUMBER_ID = (
    "phnum_8401kymgbxqcfkbb656xxmyngf9c"
)
YZ_INITIATION_PHONE_NUMBER = "+46105200413"
YZ_INITIATION_PHONE_DIGITS = "46105200413"

YZ_INITIATION_SECRET_ENV_NAME = (
    "YZ_CONVERSATION_INITIATION_SECRET"
)
YZ_INITIATION_SECRET_HEADER_NAME = (
    "X-Svir-Conversation-Initiation-Secret"
)

YZ_TIMEZONE_NAME = "Europe/Stockholm"
YZ_TIMEZONE = ZoneInfo(YZ_TIMEZONE_NAME)

YZ_WEEKDAY_OPEN = time(hour=10, minute=30)
YZ_WEEKEND_OPEN = time(hour=11, minute=30)
YZ_DAILY_CLOSE = time(hour=21, minute=0)

YZ_CLOSED_FIRST_MESSAGE = (
    "Vi har tyvärr stängt just nu. Vi har öppet vardagar "
    "halv elva till nio och helger halv tolv till nio. "
    "Välkommen åter, hej då."
)


def _require_yz_initiation_secret(
    supplied_secret: object,
) -> None:
    """
    Authenticate with a dedicated Railway environment variable.
    """

    configured_secret = os.environ.get(
        YZ_INITIATION_SECRET_ENV_NAME
    )

    if (
        not isinstance(configured_secret, str)
        or not configured_secret.strip()
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "yz_initiation_secret_not_configured",
                "message": (
                    "YZ conversation-initiation authentication "
                    "is not configured."
                ),
            },
        )

    if not isinstance(supplied_secret, str):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "yz_initiation_unauthorized",
                "message": (
                    "Conversation-initiation authentication failed."
                ),
            },
        )

    if not hmac.compare_digest(
        supplied_secret,
        configured_secret,
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "yz_initiation_unauthorized",
                "message": (
                    "Conversation-initiation authentication failed."
                ),
            },
        )


def _normalize_required_payload_string(
    payload: dict[str, object],
    *,
    field_name: str,
) -> str:
    value = payload.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_initiation_payload",
                "message": (
                    f"The required field {field_name} is missing."
                ),
            },
        )

    return value.strip()


def _normalize_yz_called_number(
    value: object,
) -> str | None:
    """
    Return canonical phone digits from a narrowly accepted called-number
    representation.

    Accepted syntax:
    - optional leading +
    - digits only
    - SIP URI with exactly one @ and a non-empty host
    - optional angle brackets around the whole SIP URI
    - optional SIP host parameters after ;

    The exact YZ-number check is performed separately.
    """

    if not isinstance(value, str):
        return None

    normalized_value = value.strip()

    if not normalized_value:
        return None

    if (
        normalized_value.startswith("<")
        and normalized_value.endswith(">")
    ):
        normalized_value = normalized_value[1:-1].strip()

    lowered_value = normalized_value.lower()
    is_sip_uri = lowered_value.startswith("sip:")

    if is_sip_uri:
        sip_target = normalized_value[4:]

        if sip_target.count("@") != 1:
            return None

        user_part, host_part = sip_target.split("@", 1)

        if (
            not host_part
            or any(
                character.isspace()
                for character in host_part
            )
            or any(
                character in host_part
                for character in ("@", "?", "#", "<", ">")
            )
        ):
            return None

        host_name = host_part.split(";", 1)[0]

        if not host_name:
            return None

        phone_part = user_part

    else:
        if any(
            character in normalized_value
            for character in (
                "@",
                ":",
                ";",
                "?",
                "#",
                "<",
                ">",
            )
        ):
            return None

        if any(
            character.isspace()
            for character in normalized_value
        ):
            return None

        phone_part = normalized_value

    if phone_part.startswith("+"):
        phone_part = phone_part[1:]

    if not re.fullmatch(r"[0-9]+", phone_part):
        return None

    return phone_part


def _validate_locked_yz_initiation_target(
    payload: dict[str, object],
) -> None:
    """
    Lock every request to the exact YZ agent.

    ElevenLabs documents agent_id for pre-call webhooks. Depending on
    telephony type, it may also include called_number and/or a phone
    resource ID. Those optional identifiers are strictly checked when
    present, while SIP calls that omit them remain compatible.
    """

    agent_id = _normalize_required_payload_string(
        payload,
        field_name="agent_id",
    )

    if agent_id != YZ_INITIATION_AGENT_ID:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "wrong_initiation_agent",
                "message": (
                    "This endpoint is locked to the YZ agent."
                ),
            },
        )

    called_number = payload.get("called_number")

    if called_number is not None:
        normalized_called_number = (
            _normalize_yz_called_number(
                called_number
            )
        )

        if (
            normalized_called_number
            != YZ_INITIATION_PHONE_DIGITS
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "wrong_initiation_phone_number",
                    "message": (
                        "This endpoint is locked to the YZ "
                        "phone number."
                    ),
                },
            )

    optional_branch_id = payload.get("branch_id")

    if (
        optional_branch_id is not None
        and optional_branch_id != YZ_INITIATION_BRANCH_ID
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "wrong_initiation_branch",
                "message": (
                    "This endpoint is locked to the YZ branch."
                ),
            },
        )

    for field_name in (
        "agent_phone_number_id",
        "phone_number_id",
    ):
        optional_phone_number_id = payload.get(
            field_name
        )

        if (
            optional_phone_number_id is not None
            and optional_phone_number_id
            != YZ_INITIATION_PHONE_NUMBER_ID
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "wrong_initiation_phone_resource",
                    "message": (
                        "This endpoint is locked to the YZ "
                        "phone resource."
                    ),
                },
            )


YZ_INITIATION_DIAGNOSTIC_FIELD_NAMES = (
    "agent_id",
    "agent_phone_number_id",
    "branch_id",
    "call_id",
    "called_number",
    "caller_id",
    "phone_number_id",
)


def _log_yz_initiation_rejection(
    *,
    payload: dict[str, object],
    error: HTTPException,
) -> None:
    """
    Log only a controlled rejection code and the names of expected
    fields that were present.

    No payload values, phone numbers, identifiers, caller data,
    secret values, headers, request bodies, or arbitrary caller
    supplied field names are logged.
    """

    if error.status_code != 403:
        return

    detail = error.detail
    rejection_code = "unknown_yz_initiation_rejection"

    if isinstance(detail, dict):
        candidate_code = detail.get("code")

        if isinstance(candidate_code, str):
            normalized_code = candidate_code.strip()

            if normalized_code:
                rejection_code = normalized_code

    present_fields = [
        field_name
        for field_name in YZ_INITIATION_DIAGNOSTIC_FIELD_NAMES
        if field_name in payload
    ]

    logger.warning(
        "YZ_INITIATION_REJECTED code=%s present_fields=%s",
        rejection_code,
        ",".join(present_fields) or "none",
    )


def _is_yz_restaurant_open(
    local_datetime: datetime,
) -> bool:
    """
    Monday-Friday: 10:30 <= time < 21:00.
    Saturday-Sunday: 11:30 <= time < 21:00.
    """

    if local_datetime.tzinfo is None:
        raise ValueError(
            "local_datetime must be timezone-aware"
        )

    stockholm_datetime = local_datetime.astimezone(
        YZ_TIMEZONE
    )
    local_time = stockholm_datetime.timetz().replace(
        tzinfo=None
    )

    opening_time = (
        YZ_WEEKDAY_OPEN
        if stockholm_datetime.weekday() < 5
        else YZ_WEEKEND_OPEN
    )

    return opening_time <= local_time < YZ_DAILY_CLOSE


def _next_yz_opening(
    local_datetime: datetime,
) -> datetime:
    """
    Return the next opening datetime in Europe/Stockholm.
    """

    if local_datetime.tzinfo is None:
        raise ValueError(
            "local_datetime must be timezone-aware"
        )

    current = local_datetime.astimezone(YZ_TIMEZONE)

    for day_offset in range(0, 8):
        candidate_date = (
            current.date()
            + timedelta(days=day_offset)
        )
        candidate_open_time = (
            YZ_WEEKDAY_OPEN
            if candidate_date.weekday() < 5
            else YZ_WEEKEND_OPEN
        )
        candidate = datetime.combine(
            candidate_date,
            candidate_open_time,
            tzinfo=YZ_TIMEZONE,
        )

        if candidate > current:
            return candidate

    raise RuntimeError(
        "Could not determine the next YZ opening time."
    )


def _build_yz_conversation_initiation_data(
    *,
    local_datetime: datetime,
) -> dict[str, object]:
    """
    Build an ElevenLabs conversation-initiation response.

    Open calls keep the normal first message. Closed calls receive a
    closed first-message override and deterministic dynamic variables.
    """

    stockholm_datetime = local_datetime.astimezone(
        YZ_TIMEZONE
    )
    is_open = _is_yz_restaurant_open(
        stockholm_datetime
    )

    dynamic_variables: dict[str, object] = {
        "restaurant_is_open": is_open,
        "restaurant_opening_status": (
            "open" if is_open else "closed"
        ),
        "restaurant_timezone": YZ_TIMEZONE_NAME,
        "restaurant_local_time_iso": (
            stockholm_datetime.isoformat(
                timespec="seconds"
            )
        ),
        "restaurant_closed_message": (
            YZ_CLOSED_FIRST_MESSAGE
        ),
    }

    response: dict[str, object] = {
        "type": "conversation_initiation_client_data",
        "dynamic_variables": dynamic_variables,
        "branch_id": YZ_INITIATION_BRANCH_ID,
        "environment": "production",
    }

    if not is_open:
        response["conversation_config_override"] = {
            "agent": {
                "first_message": YZ_CLOSED_FIRST_MESSAGE,
            },
        }
        dynamic_variables["restaurant_next_opening_iso"] = (
            _next_yz_opening(
                stockholm_datetime
            ).isoformat(timespec="seconds")
        )

    return response




@router.post(
    "/yz-thai-wok-sushi/conversation-initiation"
)
def yz_thai_wok_sushi_conversation_initiation(
    payload: dict[str, object],
    initiation_secret: Annotated[
        str | None,
        Header(
            alias=YZ_INITIATION_SECRET_HEADER_NAME
        ),
    ] = None,
) -> dict[str, object]:
    """
    Return secure opening-status initiation data for YZ.

    This route does not call ElevenLabs or any order/database service.
    """

    _require_yz_initiation_secret(
        initiation_secret
    )

    try:
        _validate_locked_yz_initiation_target(
            payload
        )

    except HTTPException as error:
        _log_yz_initiation_rejection(
            payload=payload,
            error=error,
        )
        raise

    return _build_yz_conversation_initiation_data(
        local_datetime=datetime.now(
            tz=YZ_TIMEZONE
        )
    )


@router.post(
    "/calculate-order-total",
    response_model=CalculateOrderTotalV2Response,
)
def calculate_order_total_v2(
    payload: CalculateOrderTotalV2Request,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> CalculateOrderTotalV2Response:
    """
    Calculate a verified order total using prices from the
    authenticated restaurant's active Supabase menu.

    The caller cannot provide restaurant_id, price, currency,
    or total. Railway resolves the restaurant from the secure
    tool token and reads all prices from Supabase.
    """

    try:
        result = calculate_restaurant_menu_total(
            context=context,
            request=payload,
        )

    except RestaurantMenuPricingError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return CalculateOrderTotalV2Response.model_validate(
        result
    )


@router.post(
    "/submit-order",
    response_model=SubmitOrderV2Response,
)
def submit_order_v2(
    payload: SubmitOrderV2Request,
    background_tasks: BackgroundTasks,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> SubmitOrderV2Response:
    """
    Verify and submit one restaurant-scoped order.

    Railway resolves the restaurant from X-Svir-Tool-Token,
    verifies every product and price against that restaurant's
    active Supabase menu, and saves the order atomically.

    The caller cannot provide restaurant_id, price, total,
    currency, or order status.
    """

    try:
        result = submit_restaurant_order(
            context=context,
            request=payload,
        )

    except RestaurantOrderSubmissionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    sms_context = result.pop("_sms_context", None)
    response = SubmitOrderV2Response.model_validate(result)

    try:
        if isinstance(sms_context, dict):
            sms_candidate = prepare_yz_order_confirmation_sms(
                success=response.success,
                idempotent_replay=response.idempotent_replay,
                restaurant_id=response.restaurant_id,
                order_id=response.order_id,
                customer_phone=sms_context.get("customer_phone"),
                items=sms_context.get("items"),
            )

            if sms_candidate is not None:
                background_tasks.add_task(
                    send_yz_order_confirmation_sms,
                    sms_candidate,
                )
    except Exception:
        logger.warning(
            "YZ_ORDER_SMS_FAILED order_id=%s "
            "reason=unexpected_scheduling_error",
            response.order_id,
        )

    return response


@router.post(
    "/check-order-status",
    response_model=CheckOrderStatusV2Response,
)
def check_order_status_v2(
    payload: CheckOrderStatusV2Request,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> CheckOrderStatusV2Response:
    """
    Read recent orders for one authenticated restaurant and
    one caller phone number.

    Railway resolves the restaurant from X-Svir-Tool-Token.
    This endpoint never searches another restaurant's orders
    and does not create or modify any database row.
    """

    try:
        result = check_restaurant_order_status(
            context=context,
            request=payload,
        )

    except RestaurantOrderStatusError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return CheckOrderStatusV2Response.model_validate(
        result
    )


@router.post(
    "/update-order",
    response_model=UpdateOrderV2Response,
)
def update_order_v2(
    payload: UpdateOrderV2Request,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> UpdateOrderV2Response:
    """
    Safely update one restaurant-scoped v2 order.

    Railway resolves the restaurant from X-Svir-Tool-Token,
    verifies the order, caller, current status, and revision,
    and recalculates prices from the restaurant's active menu
    whenever the product list changes.

    The caller cannot provide restaurant_id, price, total,
    currency, order status, or order revision.
    """

    try:
        result = update_restaurant_order(
            context=context,
            request=payload,
        )

    except RestaurantOrderUpdateError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return UpdateOrderV2Response.model_validate(
        result
    )


@router.post(
    "/cancel-order",
    response_model=CancelOrderV2Response,
)
def cancel_order_v2(
    payload: CancelOrderV2Request,
    context: Annotated[
        ToolRestaurantContext,
        Depends(require_restaurant_tool_context),
    ],
) -> CancelOrderV2Response:
    """
    Safely cancel one restaurant-scoped v2 order.

    Railway resolves the restaurant from X-Svir-Tool-Token,
    verifies the order, caller, current status, and revision,
    and changes the order status to cancelled without deleting
    the database row.

    The caller cannot provide restaurant_id, order status,
    order revision, price, total, or currency.
    """

    try:
        result = cancel_restaurant_order(
            context=context,
            request=payload,
        )

    except RestaurantOrderCancellationError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return CancelOrderV2Response.model_validate(
        result
    )
