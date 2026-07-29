from __future__ import annotations


TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME = (
    "svir_testkok2_calculate_order_total_v2"
)
TESTKOK2_CALCULATE_ORDER_TOTAL_V2_URL = (
    "https://web-production-f25f2.up.railway.app/"
    "v2/calculate-order-total"
)
SVIR_TOOL_TOKEN_HEADER_NAME = "X-Svir-Tool-Token"

YZ_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME = (
    "svir_yz_thai_wok_sushi_calculate_order_total_v2"
)
YZ_CALCULATE_ORDER_TOTAL_V2_URL = (
    "https://web-production-f25f2.up.railway.app/"
    "v2/calculate-order-total"
)


def build_testkok2_calculate_order_total_v2_tool_config(
    tool_token: str,
) -> dict:
    """
    Build the ElevenLabs webhook configuration for testkok2's
    secure v2 price-calculation tool.

    This function only returns a JSON-serializable dictionary. It
    does not call ElevenLabs, connect a tool to an agent, update an
    agent, or modify any external resource.

    The token is supplied at runtime and is never stored in this
    source file.
    """

    if not isinstance(tool_token, str):
        raise TypeError("tool_token must be a string")

    normalized_tool_token = tool_token.strip()

    if not normalized_tool_token:
        raise ValueError("tool_token must not be empty")

    return {
        "type": "webhook",
        "name": TESTKOK2_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME,
        "description": (
            "Calculate a verified order total for testkok2 using "
            "prices from the restaurant's active Supabase menu. "
            "Use this only to calculate a total before order "
            "confirmation. It does not create or update an order."
        ),
        "response_timeout_secs": 20,
        "api_schema": {
            "url": TESTKOK2_CALCULATE_ORDER_TOTAL_V2_URL,
            "method": "POST",
            "path_params_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "query_params_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "request_body_schema": {
                "type": "object",
                "description": (
                    "Order items whose prices must be verified "
                    "against testkok2's active menu."
                ),
                "properties": {
                    "order_items": {
                        "type": "array",
                        "description": (
                            "Products and quantities requested by "
                            "the caller."
                        ),
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "Product name as stated by "
                                        "the caller."
                                    ),
                                    "minLength": 1,
                                    "maxLength": 200,
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": (
                                        "Requested number of this "
                                        "product."
                                    ),
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                            },
                            "required": ["name", "quantity"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["order_items"],
                "additionalProperties": False,
            },
            "request_headers": {
                SVIR_TOOL_TOKEN_HEADER_NAME: normalized_tool_token,
            },
        },
        "dynamic_variables": {
            "dynamic_variable_placeholders": {},
        },
    }


def build_yz_calculate_order_total_v2_tool_config(
    tool_token: str,
) -> dict:
    """
    Build the ElevenLabs webhook configuration for YZ Thai Wok &
    Sushi's secure v2 price-calculation tool.

    This function only returns a JSON-serializable dictionary. It
    does not call ElevenLabs, connect a tool to an agent, update an
    agent, or modify any external resource.

    The token is supplied at runtime and is never stored in this
    source file.
    """

    if not isinstance(tool_token, str):
        raise TypeError("tool_token must be a string")

    normalized_tool_token = tool_token.strip()

    if not normalized_tool_token:
        raise ValueError("tool_token must not be empty")

    return {
        "type": "webhook",
        "name": YZ_CALCULATE_ORDER_TOTAL_V2_TOOL_NAME,
        "description": (
            "Calculate a verified order total for YZ Thai Wok & "
            "Sushi using prices from the restaurant's active "
            "Supabase menu. Use this only after mapping every "
            "caller phrase to the exact official Supabase menu "
            "item name. It does not create or update an order."
        ),
        "response_timeout_secs": 20,
        "api_schema": {
            "url": YZ_CALCULATE_ORDER_TOTAL_V2_URL,
            "method": "POST",
            "path_params_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "query_params_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "request_body_schema": {
                "type": "object",
                "description": (
                    "Order items whose prices must be verified "
                    "against YZ Thai Wok & Sushi's active menu."
                ),
                "properties": {
                    "order_items": {
                        "type": "array",
                        "description": (
                            "Official Supabase products and "
                            "quantities selected from the caller's "
                            "request."
                        ),
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "Exact official Supabase "
                                        "menu item name. Convert "
                                        "natural caller phrases "
                                        "before calling this tool; "
                                        "for example, send 'Pad Med "
                                        "Mamuang – Kyckling', not "
                                        "'kyckling cashew'."
                                    ),
                                    "minLength": 1,
                                    "maxLength": 200,
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": (
                                        "Requested number of this "
                                        "official menu item."
                                    ),
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                            },
                            "required": ["name", "quantity"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["order_items"],
                "additionalProperties": False,
            },
            "request_headers": {
                SVIR_TOOL_TOKEN_HEADER_NAME: normalized_tool_token,
            },
        },
        "dynamic_variables": {
            "dynamic_variable_placeholders": {},
        },
    }
