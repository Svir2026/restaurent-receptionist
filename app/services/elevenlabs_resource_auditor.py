from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from app.services.elevenlabs_client import (
    ELEVENLABS_API_BASE_URL,
    ElevenLabsClientError,
    _send_json_request,
    get_agent_configuration_snapshot,
)


def _safe_external_url(value: object) -> str | None:
    """
    Return a URL without query parameters or fragments.

    Tool URLs may contain sensitive query values, so the audit
    endpoint never returns them.
    """

    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            "",
            "",
        )
    )


def _schema_summary(value: object) -> dict[str, object]:
    """Return parameter names and required fields only."""

    if not isinstance(value, dict):
        return {
            "present": False,
            "properties": [],
            "required": [],
        }

    properties = value.get("properties")
    required = value.get("required")

    if isinstance(properties, dict):
        property_names = sorted(
            str(name) for name in properties.keys()
        )
    else:
        property_names = []

    if isinstance(required, list):
        required_names = sorted(
            str(name) for name in required
        )
    else:
        required_names = []

    return {
        "present": True,
        "properties": property_names,
        "required": required_names,
    }


def _placeholder_summary(value: object) -> dict[str, object]:
    """
    Return dynamic-variable placeholder names and safe values.

    Values whose key names indicate credentials are redacted.
    """

    if not isinstance(value, dict):
        return {}

    sensitive_names = {
        "authorization",
        "api_key",
        "apikey",
        "password",
        "secret",
        "token",
        "xi-api-key",
    }

    result: dict[str, object] = {}

    for raw_key, raw_value in value.items():
        key = str(raw_key)
        normalized_key = key.casefold().replace("-", "_")

        if normalized_key in sensitive_names:
            result[key] = "[REDACTED]"
            continue

        if isinstance(raw_value, (str, int, float, bool)):
            result[key] = raw_value
        elif raw_value is None:
            result[key] = None
        else:
            result[key] = "[COMPLEX_VALUE_HIDDEN]"

    return result


def get_tool_audit_snapshot(tool_id: str) -> dict[str, object]:
    """
    Read one ElevenLabs workspace tool without modifying it.

    The returned audit intentionally excludes header values,
    authentication values, URL query strings, and response mocks.
    """

    normalized_tool_id = tool_id.strip()

    if not normalized_tool_id:
        raise ElevenLabsClientError(
            "ElevenLabs tool ID is missing."
        )

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/tools/"
        f"{quote(normalized_tool_id, safe='')}"
    )

    payload = _send_json_request(
        url=url,
        method="GET",
        operation="read tool configuration",
    )

    returned_tool_id = payload.get("id")

    if not isinstance(returned_tool_id, str):
        raise ElevenLabsClientError(
            "ElevenLabs returned a tool without an id."
        )

    tool_config = payload.get("tool_config")

    if not isinstance(tool_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid tool_config."
        )

    api_schema = tool_config.get("api_schema")

    if not isinstance(api_schema, dict):
        api_schema = {}

    request_headers = api_schema.get("request_headers")

    if isinstance(request_headers, dict):
        request_header_names = sorted(
            str(name) for name in request_headers.keys()
        )
    else:
        request_header_names = []

    dynamic_variables = tool_config.get(
        "dynamic_variables"
    )

    if not isinstance(dynamic_variables, dict):
        dynamic_variables = {}

    placeholders = dynamic_variables.get(
        "dynamic_variable_placeholders"
    )

    return {
        "read_only": True,
        "tool_id": returned_tool_id,
        "name": tool_config.get("name"),
        "description": tool_config.get("description"),
        "type": tool_config.get("type"),
        "response_timeout_secs": tool_config.get(
            "response_timeout_secs"
        ),
        "tool_config_keys": sorted(
            str(key) for key in tool_config.keys()
        ),
        "api": {
            "present": bool(api_schema),
            "method": api_schema.get("method"),
            "url_without_query": _safe_external_url(
                api_schema.get("url")
            ),
            "path_parameters": _schema_summary(
                api_schema.get("path_params_schema")
            ),
            "query_parameters": _schema_summary(
                api_schema.get("query_params_schema")
            ),
            "request_body": _schema_summary(
                api_schema.get("request_body_schema")
            ),
            "request_header_names": request_header_names,
        },
        "dynamic_variable_placeholders": (
            _placeholder_summary(placeholders)
        ),
    }


def get_knowledge_base_document_audit_snapshot(
    *,
    document_id: str,
    agent_id: str,
) -> dict[str, object]:
    """
    Read knowledge-base document metadata without its content.
    """

    normalized_document_id = document_id.strip()
    normalized_agent_id = agent_id.strip()

    if not normalized_document_id:
        raise ElevenLabsClientError(
            "Knowledge-base document ID is missing."
        )

    if not normalized_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs agent ID is missing."
        )

    query = urlencode({"agent_id": normalized_agent_id})

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/knowledge-base/"
        f"{quote(normalized_document_id, safe='')}?{query}"
    )

    payload = _send_json_request(
        url=url,
        method="GET",
        operation="read knowledge-base document metadata",
    )

    metadata = payload.get("metadata")

    if not isinstance(metadata, dict):
        metadata = {}

    supported_usages = payload.get("supported_usages")

    if not isinstance(supported_usages, list):
        supported_usages = []

    return {
        "read_only": True,
        "document_id": payload.get("id")
        or normalized_document_id,
        "name": payload.get("name"),
        "type": payload.get("type"),
        "content_format": payload.get("content_format"),
        "supported_usages": supported_usages,
        "source_url_without_query": _safe_external_url(
            payload.get("url")
        ),
        "metadata": {
            "created_at_unix_secs": metadata.get(
                "created_at_unix_secs"
            ),
            "last_updated_at_unix_secs": metadata.get(
                "last_updated_at_unix_secs"
            ),
            "size_bytes": metadata.get("size_bytes"),
        },
    }


def get_agent_resource_audit(
    agent_id: str,
) -> dict[str, object]:
    """
    Audit all external tools and knowledge-base documents
    currently connected to an ElevenLabs agent.

    This function is entirely read-only.
    """

    normalized_agent_id = agent_id.strip()

    if not normalized_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs agent ID is missing."
        )

    configuration = get_agent_configuration_snapshot(
        normalized_agent_id
    )

    agent_section = configuration.get("agent")

    if not isinstance(agent_section, dict):
        agent_section = {}

    prompt_section = agent_section.get("prompt")

    if not isinstance(prompt_section, dict):
        prompt_section = {}

    raw_tool_ids = prompt_section.get("tool_ids")
    raw_knowledge_base = prompt_section.get("knowledge_base")

    if not isinstance(raw_tool_ids, list):
        raw_tool_ids = []

    if not isinstance(raw_knowledge_base, list):
        raw_knowledge_base = []

    tool_results: list[dict[str, object]] = []

    for raw_tool_id in raw_tool_ids:
        tool_id = str(raw_tool_id).strip()

        if not tool_id:
            continue

        try:
            tool_results.append(
                get_tool_audit_snapshot(tool_id)
            )
        except ElevenLabsClientError as error:
            tool_results.append(
                {
                    "read_only": True,
                    "tool_id": tool_id,
                    "audit_error": str(error),
                }
            )

    knowledge_base_results: list[dict[str, object]] = []

    for entry in raw_knowledge_base:
        if not isinstance(entry, dict):
            continue

        document_id = str(entry.get("id") or "").strip()

        if not document_id:
            continue

        try:
            document = (
                get_knowledge_base_document_audit_snapshot(
                    document_id=document_id,
                    agent_id=normalized_agent_id,
                )
            )
            document["usage_mode"] = entry.get("usage_mode")
            knowledge_base_results.append(document)
        except ElevenLabsClientError as error:
            knowledge_base_results.append(
                {
                    "read_only": True,
                    "document_id": document_id,
                    "name": entry.get("name"),
                    "usage_mode": entry.get("usage_mode"),
                    "audit_error": str(error),
                }
            )

    return {
        "read_only": True,
        "agent_id": normalized_agent_id,
        "agent_name": configuration.get("name"),
        "tool_count": len(raw_tool_ids),
        "knowledge_base_document_count": len(
            raw_knowledge_base
        ),
        "tools": tool_results,
        "knowledge_base": knowledge_base_results,
    }
