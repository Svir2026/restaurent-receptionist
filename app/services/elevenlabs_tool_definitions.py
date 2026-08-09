


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

YZ_SUBMIT_ORDER_V2_TOOL_NAME = (
    "svir_yz_thai_wok_sushi_submit_order_v2"
)
YZ_SUBMIT_ORDER_V2_URL = (
    "https://web-production-f25f2.up.railway.app/"
    "v2/submit-order"
)

YZ_CHECK_ORDER_STATUS_V2_TOOL_NAME = (
    "svir_yz_thai_wok_sushi_check_order_status_v2"
)
YZ_CHECK_ORDER_STATUS_V2_URL = (
    "https://web-production-f25f2.up.railway.app/"
    "v2/check-order-status"
)

YZ_UPDATE_ORDER_V2_TOOL_NAME = (
    "svir_yz_thai_wok_sushi_update_order_v2"
)
YZ_UPDATE_ORDER_V2_URL = (
    "https://web-production-f25f2.up.railway.app/"
    "v2/update-order"
)

YZ_CANCEL_ORDER_V2_TOOL_NAME = (
    "svir_yz_thai_wok_sushi_cancel_order_v2"
)
YZ_CANCEL_ORDER_V2_URL = (
    "https://web-production-f25f2.up.railway.app/"
    "v2/cancel-order"
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
            "path_params_schema": {},
            "query_params_schema": None,
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
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": (
                                        "Requested number of this "
                                        "official menu item."
                                    ),
                                },
                            },
                            "required": ["name", "quantity"],
                        },
                    }
                },
                "required": ["order_items"],
            },
            "request_headers": {
                SVIR_TOOL_TOKEN_HEADER_NAME: normalized_tool_token,
            },
        },
        "dynamic_variables": {
            "dynamic_variable_placeholders": {},
        },
    }

def build_yz_submit_order_v2_tool_config(
    tool_token: str,
) -> dict:
    """
    Build the ElevenLabs webhook configuration for YZ Thai Wok &
    Sushi's secure v2 order-submission tool.

    This function only returns a JSON-serializable dictionary. It
    does not call ElevenLabs, connect a tool to an agent, submit an
    order, update an agent, or modify any external resource.

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
        "name": YZ_SUBMIT_ORDER_V2_TOOL_NAME,
        "description": (
            "Submit one final order to YZ Thai Wok & Sushi only "
            "after the caller has explicitly confirmed the complete "
            "product summary and any confirmed special requests. "
            "Railway defaults a missing customer_name to 'Telefonkund' "
            "and a missing order_type to 'takeaway'. Only send a "
            "pickup_time when the caller explicitly provided one; "
            "never invent or infer a pickup time. Send exact official "
            "Supabase menu item names. Never send prices, totals, "
            "currency, restaurant_id, or order status; Railway verifies "
            "prices and saves the restaurant-scoped order."
        ),
        "response_timeout_secs": 25,
        "api_schema": {
            "url": YZ_SUBMIT_ORDER_V2_URL,
            "method": "POST",
            "path_params_schema": {},
            "query_params_schema": None,
            "request_body_schema": {
                "type": "object",
                "description": (
                    "Final confirmed YZ Thai Wok & Sushi order. "
                    "customer_name and order_type may be omitted so "
                    "Railway can apply the phone-order defaults. "
                    "For takeaway, pickup_time is optional and must "
                    "only be sent when explicitly stated by the caller. "
                    "For dine_in, use dine_in_time. Never send both "
                    "time fields."
                ),
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "dynamic_variable": (
                            "system__conversation_id"
                        ),
                    },
                    "customer_name": {
                        "type": "string",
                        "description": (
                            "Optional confirmed customer name. Omit "
                            "when no name was explicitly provided; "
                            "Railway then uses 'Telefonkund'."
                        ),
                    },
                    "customer_phone": {
                        "type": "string",
                        "dynamic_variable": "system__caller_id",
                    },
                    "order_type": {
                        "type": "string",
                        "description": (
                            "Optional explicit order type. Use exactly "
                            "'takeaway' or 'dine_in' only when the caller "
                            "has explicitly stated it. Omit otherwise; "
                            "Railway defaults to 'takeaway'."
                        ),
                    },
                    "order_items": {
                        "type": "array",
                        "description": (
                            "Complete final product list using exact "
                            "official Supabase menu item names."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "Exact official Supabase "
                                        "menu item name. Convert "
                                        "natural caller phrases "
                                        "before submitting."
                                    ),
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": (
                                        "Confirmed quantity of this "
                                        "official menu item."
                                    ),
                                },
                                "notes": {
                                    "type": "string",
                                    "description": (
                                        "Optional confirmed choices "
                                        "or special requests for "
                                        "this product, such as no "
                                        "onion or a free same-price "
                                        "choice."
                                    ),
                                },
                            },
                            "required": ["name", "quantity"],
                        },
                    },
                    "party_size": {
                        "type": "integer",
                        "description": (
                            "Optional confirmed number of guests "
                            "for dine_in."
                        ),
                    },
                    "dine_in_time": {
                        "type": "string",
                        "description": (
                            "Required when order_type is dine_in. "
                            "Use an ISO 8601 datetime with timezone "
                            "offset. Omit for takeaway."
                        ),
                    },
                    "pickup_time": {
                        "type": "string",
                        "description": (
                            "Optional pickup time. Send only when the "
                            "caller explicitly provided a pickup time. "
                            "Use an ISO 8601 datetime with timezone "
                            "offset. Never invent or infer a time. "
                            "Omit when no pickup time was stated and "
                            "omit for dine_in."
                        ),
                    },
                    "notes": {
                        "type": "string",
                        "description": (
                            "Optional confirmed notes applying to "
                            "the whole order."
                        ),
                    },
                },
                "required": [
                    "conversation_id",
                    "customer_phone",
                    "order_items",
                ],
            },
            "request_headers": {
                SVIR_TOOL_TOKEN_HEADER_NAME: normalized_tool_token,
            },
        },
        "dynamic_variables": {
            "dynamic_variable_placeholders": {},
        },
    }

def build_yz_check_order_status_v2_tool_config(
    tool_token: str,
) -> dict:
    """
    Build the ElevenLabs webhook configuration for YZ Thai Wok &
    Sushi's secure v2 order-status tool.

    This function only returns a JSON-serializable dictionary. It
    does not call ElevenLabs, read an order, connect a tool to an
    agent, update an agent, or modify any external resource.

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
        "name": YZ_CHECK_ORDER_STATUS_V2_TOOL_NAME,
        "description": (
            "Read the caller's recent YZ Thai Wok & Sushi orders. "
            "Use this before attempting to update or cancel an "
            "existing order, or when the caller asks whether an "
            "order was received or what its current status is. "
            "Railway identifies YZ from the secure tool token and "
            "restricts the lookup to the current caller's phone "
            "number. This tool is read-only and never creates, "
            "updates, or cancels an order."
        ),
        "response_timeout_secs": 20,
        "api_schema": {
            "url": YZ_CHECK_ORDER_STATUS_V2_URL,
            "method": "POST",
            "path_params_schema": {},
            "query_params_schema": None,
            "request_body_schema": {
                "type": "object",
                "description": (
                    "Read recent YZ Thai Wok & Sushi orders for the "
                    "current caller."
                ),
                "properties": {
                    "customer_phone": {
                        "type": "string",
                        "dynamic_variable": "system__caller_id",
                    },
                },
                "required": ["customer_phone"],
            },
            "request_headers": {
                SVIR_TOOL_TOKEN_HEADER_NAME: normalized_tool_token,
            },
        },
        "dynamic_variables": {
            "dynamic_variable_placeholders": {},
        },
    }

def build_yz_update_order_v2_tool_config(
    tool_token: str,
) -> dict:
    """
    Build the ElevenLabs webhook configuration for YZ Thai Wok &
    Sushi's secure v2 order-update tool.

    This function only returns a JSON-serializable dictionary. It
    does not call ElevenLabs, read or update an order, connect a tool
    to an agent, update an agent, or modify any external resource.

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
        "name": YZ_UPDATE_ORDER_V2_TOOL_NAME,
        "description": (
            "Update one existing YZ Thai Wok & Sushi order only "
            "after check-order-status has returned the exact order_id "
            "and the caller has explicitly confirmed the final change. "
            "Send at least one changed order field. When changing "
            "products, send the complete final product list using "
            "exact official Supabase menu item names, not only the "
            "changed item. Never send restaurant_id, prices, totals, "
            "currency, order status, or order revision; Railway "
            "verifies the caller, order, current status and prices."
        ),
        "response_timeout_secs": 25,
        "api_schema": {
            "url": YZ_UPDATE_ORDER_V2_URL,
            "method": "POST",
            "path_params_schema": {},
            "query_params_schema": None,
            "request_body_schema": {
                "type": "object",
                "description": (
                    "Confirmed update to one existing YZ Thai Wok & "
                    "Sushi order. Include order_id, automatic caller "
                    "phone, and at least one field that must change."
                ),
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": (
                            "Exact order_id returned by "
                            "check-order-status. Do not invent or "
                            "modify it."
                        ),
                    },
                    "customer_phone": {
                        "type": "string",
                        "dynamic_variable": "system__caller_id",
                    },
                    "customer_name": {
                        "type": "string",
                        "description": (
                            "New confirmed customer name. Omit when "
                            "the name is not changing."
                        ),
                    },
                    "order_type": {
                        "type": "string",
                        "description": (
                            "New order type. Use exactly 'takeaway' "
                            "or 'dine_in'. Omit when unchanged."
                        ),
                    },
                    "order_items": {
                        "type": "array",
                        "description": (
                            "Complete final product list after the "
                            "confirmed change. Send exact official "
                            "Supabase menu item names. Omit when the "
                            "products are not changing."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "Exact official Supabase "
                                        "menu item name."
                                    ),
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": (
                                        "Final confirmed quantity "
                                        "for this product."
                                    ),
                                },
                                "notes": {
                                    "type": "string",
                                    "description": (
                                        "Optional confirmed choices "
                                        "or special requests for "
                                        "this product."
                                    ),
                                },
                            },
                            "required": ["name", "quantity"],
                        },
                    },
                    "party_size": {
                        "type": "integer",
                        "description": (
                            "New confirmed guest count for dine_in. "
                            "Omit when unchanged."
                        ),
                    },
                    "dine_in_time": {
                        "type": "string",
                        "description": (
                            "New dine-in time as ISO 8601 with "
                            "timezone offset. Required when changing "
                            "order_type to dine_in. Omit otherwise."
                        ),
                    },
                    "pickup_time": {
                        "type": "string",
                        "description": (
                            "Optional new pickup time as ISO 8601 with "
                            "timezone offset. Send only when the caller "
                            "explicitly requested a pickup-time change. "
                            "Never invent or infer a time. Omit when "
                            "unchanged or when no pickup time was stated."
                        ),
                    },
                    "notes": {
                        "type": "string",
                        "description": (
                            "New confirmed notes for the whole order. "
                            "Omit when unchanged."
                        ),
                    },
                },
                "required": [
                    "order_id",
                    "customer_phone",
                ],
            },
            "request_headers": {
                SVIR_TOOL_TOKEN_HEADER_NAME: normalized_tool_token,
            },
        },
        "dynamic_variables": {
            "dynamic_variable_placeholders": {},
        },
    }

def build_yz_cancel_order_v2_tool_config(
    tool_token: str,
) -> dict:
    """
    Build the ElevenLabs webhook configuration for YZ Thai Wok &
    Sushi's secure v2 order-cancellation tool.

    This function only returns a JSON-serializable dictionary. It
    does not call ElevenLabs, read or cancel an order, connect a tool
    to an agent, update an agent, or modify any external resource.

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
        "name": YZ_CANCEL_ORDER_V2_TOOL_NAME,
        "description": (
            "Cancel one existing YZ Thai Wok & Sushi order only "
            "after check-order-status has returned the exact "
            "order_id and the caller has explicitly confirmed that "
            "the order should be cancelled. Never claim that the "
            "order is cancelled before this tool confirms success. "
            "Never send restaurant_id, order status, order revision, "
            "prices, totals, or currency; Railway verifies the "
            "restaurant, caller, current status, and revision and "
            "keeps the order history."
        ),
        "response_timeout_secs": 20,
        "api_schema": {
            "url": YZ_CANCEL_ORDER_V2_URL,
            "method": "POST",
            "path_params_schema": {},
            "query_params_schema": None,
            "request_body_schema": {
                "type": "object",
                "description": (
                    "Confirmed cancellation of one existing YZ Thai "
                    "Wok & Sushi order."
                ),
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": (
                            "Exact order_id returned by "
                            "check-order-status. Do not invent or "
                            "modify it."
                        ),
                    },
                    "customer_phone": {
                        "type": "string",
                        "dynamic_variable": "system__caller_id",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Optional confirmed reason for the "
                            "cancellation. Omit when the caller gives "
                            "no reason."
                        ),
                    },
                },
                "required": [
                    "order_id",
                    "customer_phone",
                ],
            },
            "request_headers": {
                SVIR_TOOL_TOKEN_HEADER_NAME: normalized_tool_token,
            },
        },
        "dynamic_variables": {
            "dynamic_variable_placeholders": {},
        },
    }