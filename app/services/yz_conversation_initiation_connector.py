from __future__ import annotations

import hashlib
import json
from urllib.parse import quote, urlencode

from app.services.elevenlabs_client import (
    ELEVENLABS_API_BASE_URL,
    ElevenLabsClientError,
    _extract_agent_knowledge_base_document_ids,
    _extract_agent_prompt_text,
    _extract_agent_prompt_tool_ids,
    _send_json_request,
)


YZ_AGENT_ID = "agent_3701kycttzk2e3babhgdksfcjh9g"
YZ_BRANCH_ID = "agtbrch_5501kycttzkmf9ksz96y5mbzpj3f"
YZ_EXPECTED_PRECONNECTION_VERSION_ID = (
    "agtvrsn_0501kyr5ffwpfeqv8kn8752x2g36"
)
YZ_EXPECTED_AGENT_NAME = "YZ Thai Wok & Sushi – DRAFT"

YZ_PHONE_NUMBER_ID = "phnum_8401kymgbxqcfkbb656xxmyngf9c"
YZ_PHONE_NUMBER = "+46105200413"

YZ_ACTIVE_PROMPT_SHA256 = (
    "4d3ed714511ab3853cc51f360dbabfabc0f26b979f767bd6c66f44a6fed153e0"
)

YZ_REQUIRED_TOOL_IDS = (
    "tool_1101kyqgpy9be09tep5h83km1rys",
    "tool_1801kyqkvk4zf4jrf1vc8nj3w9re",
    "tool_4401kyqn5v40fq8s0qq3wk0e6emd",
    "tool_0101kyqp6wfdep0ae1fw4ac5caqr",
    "tool_9401kyqr0j43e2at82wya08x2g6p",
)

YZ_REQUIRED_KNOWLEDGE_BASE_DOCUMENT_IDS = (
    "1atR370GTRiZivrcx8YT",
)

YZ_INITIATION_SECRET_NAME = (
    "YZ_CONVERSATION_INITIATION_SECRET"
)
YZ_INITIATION_SECRET_ID = "4ing7h7fByvSCYrqOjU9"

YZ_INITIATION_WEBHOOK_URL = (
    "https://web-production-f25f2.up.railway.app/"
    "v2/yz-thai-wok-sushi/conversation-initiation"
)
YZ_INITIATION_SECRET_HEADER_NAME = (
    "X-Svir-Conversation-Initiation-Secret"
)

YZ_INITIATION_WEBHOOK_CONFIG = {
    "url": YZ_INITIATION_WEBHOOK_URL,
    "request_headers": {
        "Content-Type": "application/json",
        YZ_INITIATION_SECRET_HEADER_NAME: {
            "secret_id": YZ_INITIATION_SECRET_ID,
        },
    },
}

YZ_CHANGED_FIELDS = (
    "platform_settings.overrides."
    "enable_conversation_initiation_client_data_from_webhook",
    "platform_settings.overrides."
    "conversation_config_override.agent.first_message",
    "platform_settings.workspace_overrides."
    "conversation_initiation_client_data_webhook",
)


class YZConversationInitiationConnectorError(RuntimeError):
    """
    Raised when the locked YZ initiation-webhook connection cannot be
    completed without changing an unapproved resource or field.
    """


def _safe_mapping(
    value: object,
) -> dict:
    if isinstance(value, dict):
        return value

    return {}


def _safe_list(
    value: object,
) -> list:
    if isinstance(value, list):
        return value

    return []


def _json_clone(
    value: object,
    *,
    field_name: str,
) -> object:
    try:
        return json.loads(json.dumps(value))

    except (
        TypeError,
        ValueError,
    ) as error:
        raise YZConversationInitiationConnectorError(
            f"{field_name} is not JSON serializable."
        ) from error


def _read_locked_yz_agent(
    *,
    operation: str,
) -> dict:
    query = urlencode(
        {
            "branch_id": YZ_BRANCH_ID,
        }
    )
    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(YZ_AGENT_ID, safe='')}?{query}"
    )

    try:
        payload = _send_json_request(
            url=url,
            method="GET",
            operation=operation,
        )

    except ElevenLabsClientError as error:
        raise YZConversationInitiationConnectorError(
            "Could not read the locked YZ agent branch."
        ) from error

    if payload.get("agent_id") != YZ_AGENT_ID:
        raise YZConversationInitiationConnectorError(
            "ElevenLabs returned an unexpected agent."
        )

    if payload.get("branch_id") != YZ_BRANCH_ID:
        raise YZConversationInitiationConnectorError(
            "ElevenLabs returned an unexpected branch."
        )

    return payload


def _read_workspace_settings(
    *,
    operation: str,
) -> dict:
    url = f"{ELEVENLABS_API_BASE_URL}/convai/settings"

    try:
        return _send_json_request(
            url=url,
            method="GET",
            operation=operation,
        )

    except ElevenLabsClientError as error:
        raise YZConversationInitiationConnectorError(
            "Could not read the ElevenLabs workspace settings."
        ) from error


def _read_exact_yz_secret(
    *,
    operation: str,
) -> dict:
    query = urlencode(
        {
            "search": YZ_INITIATION_SECRET_NAME,
            "page_size": 100,
            "dependency_limit": 100,
        }
    )
    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/secrets?"
        f"{query}"
    )

    try:
        payload = _send_json_request(
            url=url,
            method="GET",
            operation=operation,
        )

    except ElevenLabsClientError as error:
        raise YZConversationInitiationConnectorError(
            "Could not read the locked ElevenLabs workspace secret."
        ) from error

    secrets = payload.get("secrets")
    next_cursor = payload.get("next_cursor")

    if not isinstance(secrets, list):
        raise YZConversationInitiationConnectorError(
            "ElevenLabs returned an invalid workspace-secret list."
        )

    if (
        isinstance(next_cursor, str)
        and next_cursor.strip()
    ):
        raise YZConversationInitiationConnectorError(
            "The secret search was paginated, so an exact duplicate "
            "check could not be completed."
        )

    exact_matches = [
        entry
        for entry in secrets
        if isinstance(entry, dict)
        and entry.get("name") == YZ_INITIATION_SECRET_NAME
    ]

    if len(exact_matches) != 1:
        raise YZConversationInitiationConnectorError(
            "The YZ initiation secret was not found exactly once."
        )

    secret = exact_matches[0]

    if secret.get("secret_id") != YZ_INITIATION_SECRET_ID:
        raise YZConversationInitiationConnectorError(
            "The YZ initiation secret has an unexpected secret ID."
        )

    if secret.get("type") != "stored":
        raise YZConversationInitiationConnectorError(
            "The YZ initiation secret is not a stored secret."
        )

    return secret


def _extract_yz_phone_record(
    payload: dict,
) -> dict:
    phone_numbers = payload.get("phone_numbers")

    if not isinstance(phone_numbers, list):
        raise YZConversationInitiationConnectorError(
            "The YZ agent has no valid phone-number list."
        )

    if len(phone_numbers) != 1:
        raise YZConversationInitiationConnectorError(
            "The YZ agent does not have exactly one phone number."
        )

    phone_record = phone_numbers[0]

    if not isinstance(phone_record, dict):
        raise YZConversationInitiationConnectorError(
            "The YZ phone-number record is invalid."
        )

    returned_number = phone_record.get("phone_number")
    returned_phone_number_id = phone_record.get(
        "phone_number_id"
    )

    if not isinstance(returned_phone_number_id, str):
        returned_phone_number_id = phone_record.get("id")

    if returned_number != YZ_PHONE_NUMBER:
        raise YZConversationInitiationConnectorError(
            "The agent phone number does not match locked YZ."
        )

    if returned_phone_number_id != YZ_PHONE_NUMBER_ID:
        raise YZConversationInitiationConnectorError(
            "The agent phone resource does not match locked YZ."
        )

    return phone_record


def _extract_platform_target_state(
    payload: dict,
) -> dict:
    platform_settings = payload.get("platform_settings")

    if not isinstance(platform_settings, dict):
        raise YZConversationInitiationConnectorError(
            "The YZ agent has no valid platform settings."
        )

    overrides = platform_settings.get("overrides")

    if not isinstance(overrides, dict):
        raise YZConversationInitiationConnectorError(
            "The YZ agent has no valid security overrides."
        )

    conversation_override = overrides.get(
        "conversation_config_override"
    )

    if not isinstance(conversation_override, dict):
        raise YZConversationInitiationConnectorError(
            "The YZ agent has no valid conversation override settings."
        )

    agent_override = conversation_override.get("agent")

    if not isinstance(agent_override, dict):
        raise YZConversationInitiationConnectorError(
            "The YZ agent has no valid agent override settings."
        )

    webhook_feature_enabled = overrides.get(
        "enable_conversation_initiation_client_data_from_webhook"
    )
    first_message_override_enabled = agent_override.get(
        "first_message"
    )

    if not isinstance(webhook_feature_enabled, bool):
        raise YZConversationInitiationConnectorError(
            "The YZ webhook security switch is not a boolean."
        )

    if not isinstance(first_message_override_enabled, bool):
        raise YZConversationInitiationConnectorError(
            "The YZ first-message override switch is not a boolean."
        )

    workspace_overrides = platform_settings.get(
        "workspace_overrides"
    )

    if not isinstance(workspace_overrides, dict):
        raise YZConversationInitiationConnectorError(
            "The YZ agent has no valid workspace overrides."
        )

    webhook = workspace_overrides.get(
        "conversation_initiation_client_data_webhook"
    )

    return {
        "webhook_feature_enabled": webhook_feature_enabled,
        "first_message_override_enabled": (
            first_message_override_enabled
        ),
        "webhook": webhook,
    }


def _webhook_is_absent(
    value: object,
) -> bool:
    return value is None or value == {}


def _require_exact_webhook_config(
    value: object,
) -> None:
    if value != YZ_INITIATION_WEBHOOK_CONFIG:
        raise YZConversationInitiationConnectorError(
            "The existing YZ initiation webhook does not exactly match "
            "the approved URL, headers, and secret reference."
        )


def _require_no_workspace_initiation_webhook(
    workspace_settings: dict,
) -> None:
    workspace_webhook = workspace_settings.get(
        "conversation_initiation_client_data_webhook"
    )

    if not _webhook_is_absent(workspace_webhook):
        raise YZConversationInitiationConnectorError(
            "A workspace-wide conversation-initiation webhook exists. "
            "The YZ-only connector was safely blocked."
        )


def _require_locked_core_agent_state(
    payload: dict,
    *,
    require_preconnection_version: bool,
) -> None:
    if payload.get("name") != YZ_EXPECTED_AGENT_NAME:
        raise YZConversationInitiationConnectorError(
            "The YZ agent name does not match the locked target."
        )

    if payload.get("main_branch_id") != YZ_BRANCH_ID:
        raise YZConversationInitiationConnectorError(
            "The YZ main branch does not match the locked branch."
        )

    if (
        require_preconnection_version
        and payload.get("version_id")
        != YZ_EXPECTED_PRECONNECTION_VERSION_ID
    ):
        raise YZConversationInitiationConnectorError(
            "The YZ agent version changed after the approved audit. "
            "No webhook update was attempted."
        )

    _extract_yz_phone_record(payload)

    try:
        prompt_text = _extract_agent_prompt_text(payload)
        tool_ids = _extract_agent_prompt_tool_ids(payload)
        knowledge_base_ids = (
            _extract_agent_knowledge_base_document_ids(
                payload
            )
        )

    except ElevenLabsClientError as error:
        raise YZConversationInitiationConnectorError(
            "Could not validate the locked YZ prompt resources."
        ) from error

    prompt_sha256 = hashlib.sha256(
        prompt_text.encode("utf-8")
    ).hexdigest()

    if prompt_sha256 != YZ_ACTIVE_PROMPT_SHA256:
        raise YZConversationInitiationConnectorError(
            "The YZ prompt changed after the approved audit."
        )

    if tool_ids != list(YZ_REQUIRED_TOOL_IDS):
        raise YZConversationInitiationConnectorError(
            "The YZ tool list changed after the approved audit."
        )

    if knowledge_base_ids != list(
        YZ_REQUIRED_KNOWLEDGE_BASE_DOCUMENT_IDS
    ):
        raise YZConversationInitiationConnectorError(
            "The YZ knowledge-base list changed after the approved audit."
        )


def _summarize_secret_usage(
    secret: dict,
) -> dict:
    used_by = _safe_mapping(secret.get("used_by"))

    tools = _safe_list(used_by.get("tools"))
    agents = _safe_list(used_by.get("agents"))
    phone_numbers = _safe_list(
        used_by.get("phone_numbers")
    )
    mcp_servers = _safe_list(
        used_by.get("mcp_servers")
    )
    others = sorted(
        {
            entry.strip()
            for entry in _safe_list(
                used_by.get("others")
            )
            if isinstance(entry, str)
            and entry.strip()
        }
    )

    agent_ids = sorted(
        {
            agent_id
            for entry in agents
            if isinstance(entry, dict)
            for agent_id in [entry.get("id")]
            if isinstance(agent_id, str)
            and agent_id
        }
    )

    return {
        "tools_count": len(tools),
        "agent_ids": agent_ids,
        "agents_count": len(agents),
        "phone_numbers_count": len(phone_numbers),
        "mcp_servers_count": len(mcp_servers),
        "other_dependency_names": others,
        "tools_has_more": (
            used_by.get("tools_has_more") is True
        ),
        "agents_has_more": (
            used_by.get("agents_has_more") is True
        ),
        "phone_numbers_has_more": (
            used_by.get("phone_numbers_has_more") is True
        ),
    }


def _require_secret_preconnection_state(
    secret: dict,
) -> None:
    usage = _summarize_secret_usage(secret)

    if usage != {
        "tools_count": 0,
        "agent_ids": [],
        "agents_count": 0,
        "phone_numbers_count": 0,
        "mcp_servers_count": 0,
        "other_dependency_names": [],
        "tools_has_more": False,
        "agents_has_more": False,
        "phone_numbers_has_more": False,
    }:
        raise YZConversationInitiationConnectorError(
            "The YZ initiation secret already has an unexpected "
            "dependency. No webhook update was attempted."
        )


def _require_secret_connected_state(
    secret: dict,
) -> None:
    usage = _summarize_secret_usage(secret)

    if usage["tools_count"] != 0:
        raise YZConversationInitiationConnectorError(
            "The YZ initiation secret is unexpectedly used by a tool."
        )

    if usage["phone_numbers_count"] != 0:
        raise YZConversationInitiationConnectorError(
            "The YZ initiation secret is unexpectedly used by a phone."
        )

    if usage["mcp_servers_count"] != 0:
        raise YZConversationInitiationConnectorError(
            "The YZ initiation secret is unexpectedly used by MCP."
        )

    if usage["tools_has_more"]:
        raise YZConversationInitiationConnectorError(
            "The YZ secret tool dependency list was truncated."
        )

    if usage["agents_has_more"]:
        raise YZConversationInitiationConnectorError(
            "The YZ secret agent dependency list was truncated."
        )

    if usage["phone_numbers_has_more"]:
        raise YZConversationInitiationConnectorError(
            "The YZ secret phone dependency list was truncated."
        )

    if any(
        agent_id != YZ_AGENT_ID
        for agent_id in usage["agent_ids"]
    ):
        raise YZConversationInitiationConnectorError(
            "The YZ secret is unexpectedly used by another agent."
        )

    if usage["other_dependency_names"] != [
        "conversation_initiation_webhook"
    ]:
        raise YZConversationInitiationConnectorError(
            "The YZ secret does not have the exact expected webhook "
            "dependency."
        )


def _is_exact_connected_state(
    payload: dict,
) -> bool:
    target_state = _extract_platform_target_state(
        payload
    )

    if (
        target_state["webhook_feature_enabled"]
        is not True
    ):
        return False

    if (
        target_state["first_message_override_enabled"]
        is not True
    ):
        return False

    return (
        target_state["webhook"]
        == YZ_INITIATION_WEBHOOK_CONFIG
    )


def _require_exact_preconnection_state(
    payload: dict,
) -> None:
    target_state = _extract_platform_target_state(
        payload
    )

    if target_state["webhook_feature_enabled"] is not False:
        raise YZConversationInitiationConnectorError(
            "The YZ webhook feature is not in the audited off state."
        )

    if (
        target_state["first_message_override_enabled"]
        is not False
    ):
        raise YZConversationInitiationConnectorError(
            "The YZ first-message override is not in the audited "
            "off state."
        )

    if not _webhook_is_absent(
        target_state["webhook"]
    ):
        raise YZConversationInitiationConnectorError(
            "The YZ agent already contains an unexpected initiation "
            "webhook."
        )


def _build_protected_agent_state(
    payload: dict,
) -> dict:
    normalized_payload = _json_clone(
        payload,
        field_name="agent payload",
    )

    if not isinstance(normalized_payload, dict):
        raise YZConversationInitiationConnectorError(
            "The agent payload is not a JSON object."
        )

    platform_settings = normalized_payload.get(
        "platform_settings"
    )

    if not isinstance(platform_settings, dict):
        raise YZConversationInitiationConnectorError(
            "The agent platform settings are invalid."
        )

    overrides = platform_settings.get("overrides")

    if not isinstance(overrides, dict):
        raise YZConversationInitiationConnectorError(
            "The agent security overrides are invalid."
        )

    overrides.pop(
        "enable_conversation_initiation_client_data_from_webhook",
        None,
    )

    conversation_override = overrides.get(
        "conversation_config_override"
    )

    if not isinstance(conversation_override, dict):
        raise YZConversationInitiationConnectorError(
            "The conversation override settings are invalid."
        )

    agent_override = conversation_override.get(
        "agent"
    )

    if not isinstance(agent_override, dict):
        raise YZConversationInitiationConnectorError(
            "The agent override settings are invalid."
        )

    agent_override.pop("first_message", None)

    workspace_overrides = platform_settings.get(
        "workspace_overrides"
    )

    if not isinstance(workspace_overrides, dict):
        raise YZConversationInitiationConnectorError(
            "The workspace override settings are invalid."
        )

    workspace_overrides.pop(
        "conversation_initiation_client_data_webhook",
        None,
    )

    return {
        "agent_id": normalized_payload.get("agent_id"),
        "name": normalized_payload.get("name"),
        "branch_id": normalized_payload.get("branch_id"),
        "main_branch_id": normalized_payload.get(
            "main_branch_id"
        ),
        "conversation_config": normalized_payload.get(
            "conversation_config"
        ),
        "platform_settings": platform_settings,
        "phone_numbers": normalized_payload.get(
            "phone_numbers"
        ),
        "whatsapp_accounts": normalized_payload.get(
            "whatsapp_accounts"
        ),
        "workflow": normalized_payload.get("workflow"),
        "tags": normalized_payload.get("tags"),
    }


def _patch_locked_yz_initiation_settings() -> dict:
    query = urlencode(
        {
            "branch_id": YZ_BRANCH_ID,
        }
    )
    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(YZ_AGENT_ID, safe='')}?{query}"
    )
    request_body = {
        "platform_settings": {
            "overrides": {
                "conversation_config_override": {
                    "agent": {
                        "first_message": True,
                    },
                },
                (
                    "enable_conversation_initiation_"
                    "client_data_from_webhook"
                ): True,
            },
            "workspace_overrides": {
                (
                    "conversation_initiation_"
                    "client_data_webhook"
                ): YZ_INITIATION_WEBHOOK_CONFIG,
            },
        },
    }

    try:
        return _send_json_request(
            url=url,
            method="PATCH",
            operation=(
                "connect the locked YZ conversation-initiation webhook"
            ),
            body=request_body,
        )

    except ElevenLabsClientError as error:
        raise YZConversationInitiationConnectorError(
            "The YZ initiation-webhook update request failed."
        ) from error


def connect_yz_conversation_initiation_webhook() -> dict:
    """
    Connect exactly one agent-specific pre-call webhook to the locked
    YZ branch.

    Importing or deploying this module does not execute the function.
    No caller can supply an agent ID, branch ID, URL, header, secret,
    prompt, tool, knowledge-base document, phone number, or workspace
    setting.

    The function performs strict GET preconditions, one minimal agent
    PATCH only when required, and strict GET verification afterward.
    It never updates workspace-wide settings, phone resources, orders,
    Supabase, or provisioning-job state.
    """

    before_agent = _read_locked_yz_agent(
        operation="read YZ agent before initiation connection",
    )
    before_workspace = _read_workspace_settings(
        operation="read workspace settings before YZ connection",
    )
    before_secret = _read_exact_yz_secret(
        operation="read YZ initiation secret before connection",
    )

    _require_no_workspace_initiation_webhook(
        before_workspace
    )
    _require_locked_core_agent_state(
        before_agent,
        require_preconnection_version=False,
    )

    if _is_exact_connected_state(before_agent):
        _require_secret_connected_state(before_secret)

        return {
            "success": True,
            "agent_id": YZ_AGENT_ID,
            "branch_id": YZ_BRANCH_ID,
            "version_id": before_agent.get("version_id"),
            "updated_initiation_webhook": False,
            "reused_existing_connection": True,
            "changed_fields": [],
            "webhook_url": YZ_INITIATION_WEBHOOK_URL,
            "request_header_names": [
                "Content-Type",
                YZ_INITIATION_SECRET_HEADER_NAME,
            ],
            "secret_id": YZ_INITIATION_SECRET_ID,
            "secret_value_exposed": False,
            "prompt_sha256_preserved": (
                YZ_ACTIVE_PROMPT_SHA256
            ),
            "tool_ids_preserved": list(
                YZ_REQUIRED_TOOL_IDS
            ),
            "knowledge_base_document_ids_preserved": list(
                YZ_REQUIRED_KNOWLEDGE_BASE_DOCUMENT_IDS
            ),
            "phone_number_preserved": YZ_PHONE_NUMBER,
            "workspace_settings_changed": False,
            "workspace_webhook_changed": False,
            "explicit_publish_operation_performed": False,
            "supabase_changed": False,
            "order_changed": False,
            "provisioning_step_advanced": False,
        }

    _require_locked_core_agent_state(
        before_agent,
        require_preconnection_version=True,
    )
    _require_exact_preconnection_state(before_agent)
    _require_secret_preconnection_state(before_secret)

    before_protected_agent_state = (
        _build_protected_agent_state(
            before_agent
        )
    )
    before_workspace_snapshot = _json_clone(
        before_workspace,
        field_name="workspace settings",
    )

    updated_payload = (
        _patch_locked_yz_initiation_settings()
    )

    if updated_payload.get("agent_id") != YZ_AGENT_ID:
        raise YZConversationInitiationConnectorError(
            "ElevenLabs returned an unexpected agent after update."
        )

    if updated_payload.get("branch_id") != YZ_BRANCH_ID:
        raise YZConversationInitiationConnectorError(
            "ElevenLabs returned an unexpected branch after update."
        )

    after_agent = _read_locked_yz_agent(
        operation="verify YZ agent after initiation connection",
    )
    after_workspace = _read_workspace_settings(
        operation="verify workspace settings after YZ connection",
    )
    after_secret = _read_exact_yz_secret(
        operation="verify YZ initiation secret after connection",
    )

    _require_locked_core_agent_state(
        after_agent,
        require_preconnection_version=False,
    )

    if not _is_exact_connected_state(after_agent):
        raise YZConversationInitiationConnectorError(
            "The YZ branch does not contain the exact approved "
            "initiation-webhook settings after update."
        )

    _require_exact_webhook_config(
        _extract_platform_target_state(
            after_agent
        )["webhook"]
    )

    after_protected_agent_state = (
        _build_protected_agent_state(
            after_agent
        )
    )

    if (
        after_protected_agent_state
        != before_protected_agent_state
    ):
        raise YZConversationInitiationConnectorError(
            "An unapproved agent field changed during the YZ "
            "initiation-webhook update."
        )

    after_workspace_snapshot = _json_clone(
        after_workspace,
        field_name="workspace settings after update",
    )

    if after_workspace_snapshot != before_workspace_snapshot:
        raise YZConversationInitiationConnectorError(
            "Workspace-wide settings changed during the YZ-only "
            "agent update."
        )

    _require_no_workspace_initiation_webhook(
        after_workspace
    )
    _require_secret_connected_state(after_secret)

    return {
        "success": True,
        "agent_id": YZ_AGENT_ID,
        "branch_id": YZ_BRANCH_ID,
        "version_id": after_agent.get("version_id"),
        "updated_initiation_webhook": True,
        "reused_existing_connection": False,
        "changed_fields": list(YZ_CHANGED_FIELDS),
        "webhook_url": YZ_INITIATION_WEBHOOK_URL,
        "request_header_names": [
            "Content-Type",
            YZ_INITIATION_SECRET_HEADER_NAME,
        ],
        "secret_id": YZ_INITIATION_SECRET_ID,
        "secret_value_exposed": False,
        "prompt_sha256_preserved": YZ_ACTIVE_PROMPT_SHA256,
        "tool_ids_preserved": list(YZ_REQUIRED_TOOL_IDS),
        "knowledge_base_document_ids_preserved": list(
            YZ_REQUIRED_KNOWLEDGE_BASE_DOCUMENT_IDS
        ),
        "phone_number_preserved": YZ_PHONE_NUMBER,
        "workspace_settings_changed": False,
        "workspace_webhook_changed": False,
        "explicit_publish_operation_performed": False,
        "supabase_changed": False,
        "order_changed": False,
        "provisioning_step_advanced": False,
    }
