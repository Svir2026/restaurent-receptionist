from __future__ import annotations

from typing import Any

from app.services.elevenlabs_client import (
    create_webhook_tool,
    find_tool_by_exact_name,
)
from app.services.elevenlabs_tool_definitions import (
    SVIR_TOOL_TOKEN_HEADER_NAME,
    TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME,
    TESTKOK2_CALCULATE_ORDER_TOTAL_V2_URL,
    build_testkok2_calculate_order_total_v2_tool_config,
)


class ElevenLabsToolProvisioningError(RuntimeError):
    """Raised when a tool cannot be safely reused or created."""


def _require_dict(value: Any, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise ElevenLabsToolProvisioningError(
            f"ElevenLabs tool has an invalid {field_name}."
        )

    return value


def _request_body_fingerprint(schema: Any) -> dict:
    body = _require_dict(schema, "request_body_schema")

    properties = _require_dict(
        body.get("properties"),
        "request body properties",
    )

    order_items = _require_dict(
        properties.get("order_items"),
        "order_items schema",
    )

    item = _require_dict(
        order_items.get("items"),
        "order item schema",
    )

    item_properties = _require_dict(
        item.get("properties"),
        "order item properties",
    )

    name = _require_dict(
        item_properties.get("name"),
        "name schema",
    )

    quantity = _require_dict(
        item_properties.get("quantity"),
        "quantity schema",
    )

    return {
        "body_type": body.get("type"),
        "body_required": body.get("required"),
        "body_additional": body.get(
            "additionalProperties"
        ),
        "body_properties": sorted(
            properties.keys()
        ),
        "order_items_type": order_items.get("type"),
        "order_items_min": order_items.get("minItems"),
        "order_items_max": order_items.get("maxItems"),
        "item_type": item.get("type"),
        "item_required": item.get("required"),
        "item_additional": item.get(
            "additionalProperties"
        ),
        "item_properties": sorted(
            item_properties.keys()
        ),
        "name_type": name.get("type"),
        "name_min": name.get("minLength"),
        "name_max": name.get("maxLength"),
        "quantity_type": quantity.get("type"),
        "quantity_min": quantity.get("minimum"),
        "quantity_max": quantity.get("maximum"),
    }


def _expected_api_schema() -> dict:
    config = (
        build_testkok2_calculate_order_total_v2_tool_config(
            tool_token="schema-validation-only",
        )
    )

    return _require_dict(
        config.get("api_schema"),
        "expected api_schema",
    )


def _validate_tool_snapshot(tool: Any) -> dict:
    snapshot = _require_dict(
        tool,
        "tool snapshot",
    )

    if snapshot.get("name") != (
        TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME
    ):
        raise ElevenLabsToolProvisioningError(
            "ElevenLabs returned an unexpected tool name."
        )

    if snapshot.get("type") != "webhook":
        raise ElevenLabsToolProvisioningError(
            "The existing resource is not a webhook tool."
        )

    tool_id = snapshot.get("tool_id")

    if (
        not isinstance(tool_id, str)
        or not tool_id.strip()
    ):
        raise ElevenLabsToolProvisioningError(
            "The ElevenLabs tool ID is missing."
        )

    api_schema = _require_dict(
        snapshot.get("api_schema"),
        "api_schema",
    )

    expected_api_schema = _expected_api_schema()

    if api_schema.get("url") != (
        TESTKOK2_CALCULATE_ORDER_TOTAL_V2_URL
    ):
        raise ElevenLabsToolProvisioningError(
            "A tool with this name already exists "
            "with the wrong URL."
        )

    method = api_schema.get("method")

    if (
        not isinstance(method, str)
        or method.upper() != "POST"
    ):
        raise ElevenLabsToolProvisioningError(
            "A tool with this name already exists "
            "with the wrong method."
        )

    if api_schema.get("path_params_schema") != (
        expected_api_schema.get(
            "path_params_schema"
        )
    ):
        raise ElevenLabsToolProvisioningError(
            "The existing tool has unexpected "
            "path parameters."
        )

    if api_schema.get("query_params_schema") != (
        expected_api_schema.get(
            "query_params_schema"
        )
    ):
        raise ElevenLabsToolProvisioningError(
            "The existing tool has unexpected "
            "query parameters."
        )

    header_names = api_schema.get(
        "request_header_names"
    )

    if not isinstance(header_names, list):
        raise ElevenLabsToolProvisioningError(
            "The existing tool request headers are invalid."
        )

    normalized_header_names = {
        str(name).strip().lower()
        for name in header_names
        if str(name).strip()
    }

    if normalized_header_names != {
        SVIR_TOOL_TOKEN_HEADER_NAME.lower()
    }:
        raise ElevenLabsToolProvisioningError(
            "The existing tool has unexpected "
            "request headers."
        )

    existing_fingerprint = (
        _request_body_fingerprint(
            api_schema.get(
                "request_body_schema"
            )
        )
    )

    expected_fingerprint = (
        _request_body_fingerprint(
            expected_api_schema.get(
                "request_body_schema"
            )
        )
    )

    if existing_fingerprint != expected_fingerprint:
        raise ElevenLabsToolProvisioningError(
            "The existing tool has an unexpected "
            "request body schema."
        )

    return snapshot


def ensure_testkok2_calculate_order_total_v2_tool(
    tool_token: str | None = None,
) -> dict:
    """
    Reuse or create testkok2's restaurant-isolated
    v2 price-calculation tool.

    The exact name is searched first. A correctly
    configured tool is reused without needing the
    full token again.

    The token is required only if the tool is missing
    and must be created.

    This function does not connect the tool to an
    agent and does not update Supabase or any
    provisioning step.
    """

    existing_tool = find_tool_by_exact_name(
        TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME
    )

    if existing_tool is not None:
        verified_tool = _validate_tool_snapshot(
            existing_tool
        )

        return {
            "success": True,
            "created_new_tool": False,
            "reused_existing_tool": True,
            "tool_id": verified_tool["tool_id"],
            "tool_name": verified_tool["name"],
        }

    if (
        not isinstance(tool_token, str)
        or not tool_token.strip()
    ):
        raise ElevenLabsToolProvisioningError(
            "A tool token is required to create "
            "the missing tool."
        )

    tool_config = (
        build_testkok2_calculate_order_total_v2_tool_config(
            tool_token=tool_token,
        )
    )

    created_tool = create_webhook_tool(
        tool_config
    )

    verified_tool = _validate_tool_snapshot(
        created_tool
    )

    return {
        "success": True,
        "created_new_tool": True,
        "reused_existing_tool": False,
        "tool_id": verified_tool["tool_id"],
        "tool_name": verified_tool["name"],
    }
