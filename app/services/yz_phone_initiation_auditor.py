from __future__ import annotations

from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from app.services.elevenlabs_client import (
    ELEVENLABS_API_BASE_URL,
    ElevenLabsClientError,
    _send_json_request,
)


YZ_AGENT_ID = "agent_3701kycttzk2e3babhgdksfcjh9g"
YZ_BRANCH_ID = "agtbrch_5501kycttzkmf9ksz96y5mbzpj3f"
YZ_PHONE_NUMBER = "+46105200413"


class YZPhoneInitiationAuditError(RuntimeError):
    """
    Raised when the read-only YZ phone-initiation audit cannot safely
    identify or validate the exact configured resource.
    """


def _normalize_required_string(
    value: object,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise YZPhoneInitiationAuditError(
            f"{field_name} must be a string."
        )

    normalized_value = value.strip()

    if not normalized_value:
        raise YZPhoneInitiationAuditError(
            f"{field_name} is missing."
        )

    return normalized_value


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


def _safe_sorted_keys(
    value: object,
) -> list[str]:
    mapping = _safe_mapping(value)

    return sorted(str(key) for key in mapping.keys())


def _url_without_query_or_credentials(
    value: object,
) -> str | None:
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()

    if not normalized_value:
        return None

    try:
        parsed = urlsplit(normalized_value)

    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    host = parsed.hostname

    if parsed.port is not None:
        host = f"{host}:{parsed.port}"

    return urlunsplit(
        (
            parsed.scheme,
            host,
            parsed.path,
            "",
            "",
        )
    )


def _summarize_webhook(
    value: object,
) -> dict:
    webhook = _safe_mapping(value)
    request_headers = _safe_mapping(
        webhook.get("request_headers")
    )

    return {
        "present": bool(webhook),
        "url_without_query": (
            _url_without_query_or_credentials(
                webhook.get("url")
            )
        ),
        "request_header_names": sorted(
            str(name) for name in request_headers.keys()
        ),
        "request_header_value_count": len(request_headers),
        "header_values_exposed": False,
    }


def _find_boolean_by_key(
    value: object,
    *,
    target_key: str,
) -> bool | None:
    if isinstance(value, dict):
        direct_value = value.get(target_key)

        if isinstance(direct_value, bool):
            return direct_value

        for nested_value in value.values():
            result = _find_boolean_by_key(
                nested_value,
                target_key=target_key,
            )

            if result is not None:
                return result

    elif isinstance(value, list):
        for nested_value in value:
            result = _find_boolean_by_key(
                nested_value,
                target_key=target_key,
            )

            if result is not None:
                return result

    return None


def _find_exact_phone_record(
    phone_numbers: object,
) -> dict:
    exact_matches: list[dict] = []

    for entry in _safe_list(phone_numbers):
        if not isinstance(entry, dict):
            continue

        returned_number = entry.get("phone_number")

        if (
            isinstance(returned_number, str)
            and returned_number.strip() == YZ_PHONE_NUMBER
        ):
            exact_matches.append(entry)

    if len(exact_matches) != 1:
        raise YZPhoneInitiationAuditError(
            "The exact YZ phone number was not found exactly once "
            "on the locked agent branch."
        )

    return exact_matches[0]


def _read_locked_yz_agent() -> dict:
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
            operation="read locked YZ agent branch for phone audit",
        )

    except ElevenLabsClientError as error:
        raise YZPhoneInitiationAuditError(
            "Could not read the locked YZ agent branch."
        ) from error

    if payload.get("agent_id") != YZ_AGENT_ID:
        raise YZPhoneInitiationAuditError(
            "ElevenLabs returned an unexpected agent."
        )

    if payload.get("branch_id") != YZ_BRANCH_ID:
        raise YZPhoneInitiationAuditError(
            "ElevenLabs returned an unexpected branch."
        )

    return payload


def _read_phone_number(
    phone_number_id: str,
) -> dict:
    normalized_phone_number_id = _normalize_required_string(
        phone_number_id,
        field_name="phone_number_id",
    )

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/phone-numbers/"
        f"{quote(normalized_phone_number_id, safe='')}"
    )

    try:
        payload = _send_json_request(
            url=url,
            method="GET",
            operation="read YZ phone-number configuration",
        )

    except ElevenLabsClientError as error:
        raise YZPhoneInitiationAuditError(
            "Could not read the YZ phone-number configuration."
        ) from error

    returned_phone_number_id = payload.get("phone_number_id")

    if (
        isinstance(returned_phone_number_id, str)
        and returned_phone_number_id.strip()
        != normalized_phone_number_id
    ):
        raise YZPhoneInitiationAuditError(
            "ElevenLabs returned an unexpected phone-number resource."
        )

    returned_number = payload.get("phone_number")

    if (
        not isinstance(returned_number, str)
        or returned_number.strip() != YZ_PHONE_NUMBER
    ):
        raise YZPhoneInitiationAuditError(
            "The phone-number resource does not match YZ's locked "
            "telephone number."
        )

    return payload


def _read_workspace_settings() -> dict:
    url = f"{ELEVENLABS_API_BASE_URL}/convai/settings"

    try:
        return _send_json_request(
            url=url,
            method="GET",
            operation="read workspace conversation settings",
        )

    except ElevenLabsClientError as error:
        raise YZPhoneInitiationAuditError(
            "Could not read workspace conversation settings."
        ) from error


def get_yz_phone_initiation_audit() -> dict:
    """
    Read and safely summarize YZ's telephony and conversation-initiation
    configuration.

    GET requests only. Secret header values, API keys, webhook query
    parameters, SIP credentials, and trunk values are never returned.
    """

    agent_payload = _read_locked_yz_agent()
    agent_phone_record = _find_exact_phone_record(
        agent_payload.get("phone_numbers")
    )

    phone_number_id = agent_phone_record.get("phone_number_id")

    if not isinstance(phone_number_id, str):
        phone_number_id = agent_phone_record.get("id")

    normalized_phone_number_id = _normalize_required_string(
        phone_number_id,
        field_name="phone_number_id",
    )

    phone_payload = _read_phone_number(
        normalized_phone_number_id
    )
    workspace_settings = _read_workspace_settings()

    assigned_agent = _safe_mapping(
        phone_payload.get("assigned_agent")
    )
    platform_settings = _safe_mapping(
        agent_payload.get("platform_settings")
    )
    security_overrides = _safe_mapping(
        platform_settings.get("overrides")
    )
    agent_workspace_overrides = _safe_mapping(
        platform_settings.get("workspace_overrides")
    )

    agent_webhook = _summarize_webhook(
        agent_workspace_overrides.get(
            "conversation_initiation_client_data_webhook"
        )
    )
    workspace_webhook = _summarize_webhook(
        workspace_settings.get(
            "conversation_initiation_client_data_webhook"
        )
    )

    webhook_feature_enabled = _find_boolean_by_key(
        security_overrides,
        target_key=(
            "enable_conversation_initiation_client_data_from_webhook"
        ),
    )
    first_message_override_enabled = _find_boolean_by_key(
        security_overrides,
        target_key="first_message",
    )

    if agent_webhook["present"]:
        effective_webhook_source = "agent_workspace_override"
        effective_webhook = agent_webhook

    elif workspace_webhook["present"]:
        effective_webhook_source = "workspace"
        effective_webhook = workspace_webhook

    else:
        effective_webhook_source = "none"
        effective_webhook = {
            "present": False,
            "url_without_query": None,
            "request_header_names": [],
            "request_header_value_count": 0,
            "header_values_exposed": False,
        }

    assigned_agent_id = assigned_agent.get("agent_id")
    assigned_branch_id = assigned_agent.get("branch_id")

    assigned_to_locked_agent = (
        assigned_agent_id == YZ_AGENT_ID
    )
    assigned_to_locked_branch = (
        assigned_branch_id == YZ_BRANCH_ID
    )

    initiation_webhook_ready = (
        webhook_feature_enabled is True
        and effective_webhook["present"] is True
    )
    first_message_override_ready = (
        first_message_override_enabled is True
    )

    return {
        "success": True,
        "read_only": True,
        "locked_target": {
            "agent_id": YZ_AGENT_ID,
            "branch_id": YZ_BRANCH_ID,
            "phone_number": YZ_PHONE_NUMBER,
        },
        "agent": {
            "name": agent_payload.get("name"),
            "version_id": agent_payload.get("version_id"),
            "main_branch_id": agent_payload.get("main_branch_id"),
            "phone_number_count": len(
                _safe_list(agent_payload.get("phone_numbers"))
            ),
            "platform_setting_keys": _safe_sorted_keys(
                platform_settings
            ),
        },
        "phone": {
            "phone_number_id": normalized_phone_number_id,
            "phone_number": phone_payload.get("phone_number"),
            "label": phone_payload.get("label"),
            "provider": phone_payload.get("provider"),
            "environment": phone_payload.get("environment"),
            "livekit_stack": phone_payload.get("livekit_stack"),
            "store_sip_messages": phone_payload.get(
                "store_sip_messages"
            ),
            "resource_keys": _safe_sorted_keys(phone_payload),
            "inbound_trunk_config": {
                "present": isinstance(
                    phone_payload.get("inbound_trunk_config"),
                    dict,
                ),
                "keys": _safe_sorted_keys(
                    phone_payload.get("inbound_trunk_config")
                ),
                "values_exposed": False,
            },
            "outbound_trunk_config": {
                "present": isinstance(
                    phone_payload.get("outbound_trunk_config"),
                    dict,
                ),
                "keys": _safe_sorted_keys(
                    phone_payload.get("outbound_trunk_config")
                ),
                "values_exposed": False,
            },
        },
        "assigned_agent": {
            "present": bool(assigned_agent),
            "agent_id": assigned_agent_id,
            "agent_name": assigned_agent.get("agent_name"),
            "environment": assigned_agent.get("environment"),
            "branch_id": assigned_branch_id,
            "matches_locked_agent": assigned_to_locked_agent,
            "matches_locked_branch": assigned_to_locked_branch,
        },
        "conversation_initiation": {
            "security_override_keys": _safe_sorted_keys(
                security_overrides
            ),
            "webhook_feature_enabled": webhook_feature_enabled,
            "first_message_override_enabled": (
                first_message_override_enabled
            ),
            "agent_webhook": agent_webhook,
            "workspace_webhook": workspace_webhook,
            "effective_webhook_source": effective_webhook_source,
            "effective_webhook": effective_webhook,
            "initiation_webhook_ready": initiation_webhook_ready,
            "first_message_override_ready": (
                first_message_override_ready
            ),
            "technical_closed_message_path_ready": (
                initiation_webhook_ready
                and first_message_override_ready
                and assigned_to_locked_agent
                and assigned_to_locked_branch
            ),
        },
        "safety": {
            "external_requests_made": 3,
            "http_methods_used": ["GET"],
            "secret_header_values_exposed": False,
            "webhook_query_parameters_exposed": False,
            "sip_or_trunk_values_exposed": False,
            "agent_changed": False,
            "phone_connection_changed": False,
            "workspace_settings_changed": False,
            "order_changed": False,
            "supabase_changed": False,
            "provisioning_step_advanced": False,
        },
    }
