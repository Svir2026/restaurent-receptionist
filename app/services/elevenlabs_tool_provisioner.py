from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from typing import Any

from app.services.elevenlabs_client import (
    ElevenLabsClientError,
    create_webhook_tool,
    find_tool_by_exact_name,
)
from app.services.elevenlabs_tool_definitions import (
    SVIR_TOOL_TOKEN_HEADER_NAME,
    TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME,
    TESTKOK2_CALCULATE_ORDER_TOTAL_V2_URL,
    YZ_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME,
    YZ_CALCULATE_ORDER_TOTAL_V2_URL,
    build_testkok2_calculate_order_total_v2_tool_config,
    build_yz_calculate_order_total_v2_tool_config,
)


YZ_TOOL_TOKEN_REDACTION_PATTERN = re.compile(
    r"svir_tool_[0-9a-fA-F]{64}"
)


def _sanitize_yz_tool_creation_error(
    error: Exception,
) -> str:
    """
    Return ElevenLabs' error text with any complete Svir tool token
    replaced before it is exposed through the protected YZ route.
    """

    message = str(error).strip()

    if not message:
        return "ElevenLabs returned no diagnostic message."

    return YZ_TOOL_TOKEN_REDACTION_PATTERN.sub(
        "[REDACTED_TOOL_TOKEN]",
        message,
    )


class ElevenLabsToolProvisioningError(RuntimeError):
    """Raised when a Svir workspace tool cannot be safely ensured."""


def _require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ElevenLabsToolProvisioningError(
            f"ElevenLabs tool field '{field_name}' is invalid."
        )

    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    expected_keys: set[str],
    field_name: str,
) -> None:
    actual_keys = {str(key) for key in value.keys()}

    if actual_keys != expected_keys:
        raise ElevenLabsToolProvisioningError(
            f"ElevenLabs tool field '{field_name}' has an unsafe schema."
        )


def _require_exact_required(
    value: Any,
    *,
    expected_values: set[str],
    field_name: str,
) -> None:
    if not isinstance(value, list):
        raise ElevenLabsToolProvisioningError(
            f"ElevenLabs tool field '{field_name}' is invalid."
        )

    actual_values = {str(item) for item in value}

    if actual_values != expected_values or len(value) != len(expected_values):
        raise ElevenLabsToolProvisioningError(
            f"ElevenLabs tool field '{field_name}' has an unsafe schema."
        )


def _validate_empty_object_schema(
    schema: Any,
    *,
    field_name: str,
) -> None:
    normalized_schema = _require_mapping(
        schema,
        field_name=field_name,
    )

    if normalized_schema.get("type") != "object":
        raise ElevenLabsToolProvisioningError(
            f"ElevenLabs tool field '{field_name}' must be an object schema."
        )

    properties = _require_mapping(
        normalized_schema.get("properties"),
        field_name=f"{field_name}.properties",
    )
    _require_exact_keys(
        properties,
        expected_keys=set(),
        field_name=f"{field_name}.properties",
    )
    _require_exact_required(
        normalized_schema.get("required"),
        expected_values=set(),
        field_name=f"{field_name}.required",
    )

    if normalized_schema.get("additionalProperties") is not False:
        raise ElevenLabsToolProvisioningError(
            f"ElevenLabs tool field '{field_name}' allows extra values."
        )


def _validate_calculate_request_body_schema(schema: Any) -> None:
    body_schema = _require_mapping(
        schema,
        field_name="api_schema.request_body_schema",
    )

    if body_schema.get("type") != "object":
        raise ElevenLabsToolProvisioningError(
            "The calculate-order-total request body must be an object."
        )

    body_properties = _require_mapping(
        body_schema.get("properties"),
        field_name="api_schema.request_body_schema.properties",
    )
    _require_exact_keys(
        body_properties,
        expected_keys={"order_items"},
        field_name="api_schema.request_body_schema.properties",
    )
    _require_exact_required(
        body_schema.get("required"),
        expected_values={"order_items"},
        field_name="api_schema.request_body_schema.required",
    )

    if body_schema.get("additionalProperties") is not False:
        raise ElevenLabsToolProvisioningError(
            "The calculate-order-total request body allows extra fields."
        )

    order_items = _require_mapping(
        body_properties.get("order_items"),
        field_name="order_items",
    )

    if order_items.get("type") != "array":
        raise ElevenLabsToolProvisioningError(
            "The calculate-order-total order_items field must be an array."
        )

    if order_items.get("minItems") != 1 or order_items.get("maxItems") != 100:
        raise ElevenLabsToolProvisioningError(
            "The calculate-order-total order_items limits are invalid."
        )

    item_schema = _require_mapping(
        order_items.get("items"),
        field_name="order_items.items",
    )

    if item_schema.get("type") != "object":
        raise ElevenLabsToolProvisioningError(
            "Each calculate-order-total order item must be an object."
        )

    item_properties = _require_mapping(
        item_schema.get("properties"),
        field_name="order_items.items.properties",
    )
    _require_exact_keys(
        item_properties,
        expected_keys={"name", "quantity"},
        field_name="order_items.items.properties",
    )
    _require_exact_required(
        item_schema.get("required"),
        expected_values={"name", "quantity"},
        field_name="order_items.items.required",
    )

    if item_schema.get("additionalProperties") is not False:
        raise ElevenLabsToolProvisioningError(
            "The calculate-order-total order item allows extra fields."
        )

    name_schema = _require_mapping(
        item_properties.get("name"),
        field_name="order_items.items.name",
    )

    if (
        name_schema.get("type") != "string"
        or name_schema.get("minLength") != 1
        or name_schema.get("maxLength") != 200
    ):
        raise ElevenLabsToolProvisioningError(
            "The calculate-order-total item name schema is invalid."
        )

    quantity_schema = _require_mapping(
        item_properties.get("quantity"),
        field_name="order_items.items.quantity",
    )

    if (
        quantity_schema.get("type") != "integer"
        or quantity_schema.get("minimum") != 1
        or quantity_schema.get("maximum") != 100
    ):
        raise ElevenLabsToolProvisioningError(
            "The calculate-order-total quantity schema is invalid."
        )


def _validate_calculate_order_total_tool_snapshot(
    tool_snapshot: Mapping[str, Any],
) -> None:
    tool_id = tool_snapshot.get("tool_id")

    if not isinstance(tool_id, str) or not tool_id.strip():
        raise ElevenLabsToolProvisioningError(
            "ElevenLabs returned a tool without a valid tool ID."
        )

    if tool_snapshot.get("name") != TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME:
        raise ElevenLabsToolProvisioningError(
            "ElevenLabs returned a different tool name."
        )

    if tool_snapshot.get("type") != "webhook":
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool is not a webhook tool."
        )

    api_schema = _require_mapping(
        tool_snapshot.get("api_schema"),
        field_name="api_schema",
    )

    if api_schema.get("url") != TESTKOK2_CALCULATE_ORDER_TOTAL_V2_URL:
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool points to the wrong URL."
        )

    method = api_schema.get("method")

    if not isinstance(method, str) or method.strip().upper() != "POST":
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool uses the wrong HTTP method."
        )

    _validate_empty_object_schema(
        api_schema.get("path_params_schema"),
        field_name="api_schema.path_params_schema",
    )
    _validate_empty_object_schema(
        api_schema.get("query_params_schema"),
        field_name="api_schema.query_params_schema",
    )
    _validate_calculate_request_body_schema(
        api_schema.get("request_body_schema")
    )

    header_names = api_schema.get("request_header_names")

    if not isinstance(header_names, list):
        raise ElevenLabsToolProvisioningError(
            "ElevenLabs returned invalid request-header metadata."
        )

    normalized_header_names = {
        str(header_name).strip().lower()
        for header_name in header_names
    }
    expected_header_names = {
        SVIR_TOOL_TOKEN_HEADER_NAME.lower(),
    }

    if (
        normalized_header_names != expected_header_names
        or len(header_names) != len(expected_header_names)
    ):
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool has unsafe request headers."
        )


def _build_result(
    tool_snapshot: Mapping[str, Any],
    *,
    created_new_tool: bool,
) -> dict:
    return {
        "success": True,
        "tool_id": str(tool_snapshot["tool_id"]),
        "name": TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME,
        "created_new_tool": created_new_tool,
        "reused_existing_tool": not created_new_tool,
        "tool": dict(tool_snapshot),
    }


def ensure_testkok2_calculate_order_total_v2_tool(
    tool_token_provider: Callable[[], str],
) -> dict:
    """
    Idempotently ensure testkok2's secure price-calculation tool.

    The function first searches by the exact unique tool name. A
    matching tool is reused only after its URL, method, schemas and
    request-header name have been verified. The token provider is
    called only when no matching tool exists and a new tool must be
    created. This allows safe retries without storing or exposing the
    full token after successful creation.

    This function never connects a tool to an agent, never updates an
    agent, and never changes or removes any existing Lebanon resource.
    """

    if not callable(tool_token_provider):
        raise TypeError("tool_token_provider must be callable")

    try:
        existing_tool = find_tool_by_exact_name(
            TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME
        )
    except ElevenLabsClientError as error:
        raise ElevenLabsToolProvisioningError(
            "Could not safely search ElevenLabs workspace tools."
        ) from error

    if existing_tool is not None:
        _validate_calculate_order_total_tool_snapshot(existing_tool)
        return _build_result(
            existing_tool,
            created_new_tool=False,
        )

    try:
        tool_token = tool_token_provider()
        desired_config = (
            build_testkok2_calculate_order_total_v2_tool_config(
                tool_token
            )
        )
    except (TypeError, ValueError) as error:
        raise ElevenLabsToolProvisioningError(
            "Could not obtain a valid runtime tool token."
        ) from error

    try:
        created_tool = create_webhook_tool(desired_config)
    except ElevenLabsClientError as error:
        raise ElevenLabsToolProvisioningError(
            "Could not create the secure ElevenLabs workspace tool."
        ) from error

    _validate_calculate_order_total_tool_snapshot(created_tool)

    return _build_result(
        created_tool,
        created_new_tool=True,
    )


def _validate_yz_calculate_order_total_tool_snapshot(
    tool_snapshot: Mapping[str, Any],
) -> None:
    tool_id = tool_snapshot.get("tool_id")

    if not isinstance(tool_id, str) or not tool_id.strip():
        raise ElevenLabsToolProvisioningError(
            "ElevenLabs returned a tool without a valid tool ID."
        )

    if tool_snapshot.get("name") != YZ_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME:
        raise ElevenLabsToolProvisioningError(
            "ElevenLabs returned a different tool name."
        )

    if tool_snapshot.get("type") != "webhook":
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool is not a webhook tool."
        )

    api_schema = _require_mapping(
        tool_snapshot.get("api_schema"),
        field_name="api_schema",
    )

    if api_schema.get("url") != YZ_CALCULATE_ORDER_TOTAL_V2_URL:
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool points to the wrong URL."
        )

    method = api_schema.get("method")

    if not isinstance(method, str) or method.strip().upper() != "POST":
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool uses the wrong HTTP method."
        )

    _validate_empty_object_schema(
        api_schema.get("path_params_schema"),
        field_name="api_schema.path_params_schema",
    )
    _validate_empty_object_schema(
        api_schema.get("query_params_schema"),
        field_name="api_schema.query_params_schema",
    )
    _validate_calculate_request_body_schema(
        api_schema.get("request_body_schema")
    )

    header_names = api_schema.get("request_header_names")

    if not isinstance(header_names, list):
        raise ElevenLabsToolProvisioningError(
            "ElevenLabs returned invalid request-header metadata."
        )

    normalized_header_names = {
        str(header_name).strip().lower()
        for header_name in header_names
    }
    expected_header_names = {
        SVIR_TOOL_TOKEN_HEADER_NAME.lower(),
    }

    if (
        normalized_header_names != expected_header_names
        or len(header_names) != len(expected_header_names)
    ):
        raise ElevenLabsToolProvisioningError(
            "The existing ElevenLabs tool has unsafe request headers."
        )


def _build_yz_result(
    tool_snapshot: Mapping[str, Any],
    *,
    created_new_tool: bool,
) -> dict:
    return {
        "success": True,
        "tool_id": str(tool_snapshot["tool_id"]),
        "name": YZ_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME,
        "created_new_tool": created_new_tool,
        "reused_existing_tool": not created_new_tool,
        "tool": dict(tool_snapshot),
    }


def ensure_yz_calculate_order_total_v2_tool(
    tool_token_provider: Callable[[], str],
) -> dict:
    """
    Idempotently ensure YZ Thai Wok & Sushi's secure
    price-calculation tool.

    The function first searches by the exact unique tool name. A
    matching tool is reused only after its URL, method, schemas and
    request-header name have been verified. The token provider is
    called only when no matching tool exists and a new tool must be
    created. This allows safe retries without storing or exposing the
    full token after successful creation.

    This function never connects a tool to an agent, never updates an
    agent, and never changes or removes any existing Lebanon resource.
    """

    if not callable(tool_token_provider):
        raise TypeError("tool_token_provider must be callable")

    try:
        existing_tool = find_tool_by_exact_name(
            YZ_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME
        )
    except ElevenLabsClientError as error:
        raise ElevenLabsToolProvisioningError(
            "Could not safely search ElevenLabs workspace tools."
        ) from error

    if existing_tool is not None:
        _validate_yz_calculate_order_total_tool_snapshot(
            existing_tool
        )
        return _build_yz_result(
            existing_tool,
            created_new_tool=False,
        )

    try:
        tool_token = tool_token_provider()
        desired_config = (
            build_yz_calculate_order_total_v2_tool_config(
                tool_token
            )
        )
    except (TypeError, ValueError) as error:
        raise ElevenLabsToolProvisioningError(
            "Could not obtain a valid runtime tool token."
        ) from error

    try:
        created_tool = create_webhook_tool(desired_config)
    except ElevenLabsClientError as error:
        sanitized_error = _sanitize_yz_tool_creation_error(
            error
        )
        raise ElevenLabsToolProvisioningError(
            "Could not create the secure ElevenLabs workspace "
            f"tool. ElevenLabs details: {sanitized_error}"
        ) from error

    _validate_yz_calculate_order_total_tool_snapshot(created_tool)

    return _build_yz_result(
        created_tool,
        created_new_tool=True,
    )
