import hashlib
import json
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.core.config import settings


ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsClientError(RuntimeError):
    """Raised when communication with ElevenLabs fails."""


def _send_json_request(
    *,
    url: str,
    method: str,
    operation: str,
    body: Optional[dict] = None,
) -> dict:
    """
    Send an authenticated JSON request to ElevenLabs.

    API keys are read only from Railway environment variables.
    """

    api_key = settings.elevenlabs_api_key.get_secret_value()

    headers = {
        "Accept": "application/json",
        "xi-api-key": api_key,
    }

    request_data = None

    if body is not None:
        headers["Content-Type"] = "application/json"
        request_data = json.dumps(body).encode("utf-8")

    request = Request(
        url,
        data=request_data,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=20) as response:
            raw_response = response.read().decode("utf-8")
            payload = json.loads(raw_response)

    except HTTPError as exc:
        detail = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise ElevenLabsClientError(
            f"ElevenLabs {operation} returned HTTP "
            f"{exc.code}: {detail[:300]}"
        ) from exc

    except (
        URLError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ElevenLabsClientError(
            f"Could not {operation} in ElevenLabs."
        ) from exc

    if not isinstance(payload, dict):
        raise ElevenLabsClientError(
            f"ElevenLabs returned an unexpected response "
            f"while attempting to {operation}."
        )

    return payload


def get_agent_summary(agent_id: str) -> dict:
    """
    Read an ElevenLabs agent without changing it.
    """

    normalized_agent_id = agent_id.strip()

    if not normalized_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs agent ID is missing."
        )

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(normalized_agent_id, safe='')}"
    )

    payload = _send_json_request(
        url=url,
        method="GET",
        operation="read agent",
    )

    returned_agent_id = payload.get("agent_id")

    if not isinstance(returned_agent_id, str):
        raise ElevenLabsClientError(
            "ElevenLabs returned an agent without an agent_id."
        )

    phone_numbers = payload.get("phone_numbers") or []

    if not isinstance(phone_numbers, list):
        phone_numbers = []

    return {
        "agent_id": returned_agent_id,
        "name": payload.get("name"),
        "phone_number_count": len(phone_numbers),
        "branch_id": payload.get("branch_id"),
        "version_id": payload.get("version_id"),
    }


def get_template_agent_summary() -> dict:
    """
    Read the configured template agent without changing it.
    """

    template_agent_id = (
        settings.elevenlabs_template_agent_id.strip()
    )

    return get_agent_summary(template_agent_id)


def get_agent_configuration_snapshot(agent_id: str) -> dict:
    """
    Read the fields needed before a controlled agent update.

    This function is read-only. It returns the current prompt,
    first message, voice settings, tool references, and a safe
    structural summary of workflow and platform settings.
    """

    normalized_agent_id = agent_id.strip()

    if not normalized_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs agent ID is missing."
        )

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(normalized_agent_id, safe='')}"
    )

    payload = _send_json_request(
        url=url,
        method="GET",
        operation="read agent configuration",
    )

    returned_agent_id = payload.get("agent_id")

    if not isinstance(returned_agent_id, str):
        raise ElevenLabsClientError(
            "ElevenLabs returned an agent without an agent_id."
        )

    if returned_agent_id.strip() != normalized_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different agent than requested."
        )

    conversation_config = payload.get("conversation_config")

    if not isinstance(conversation_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid conversation_config."
        )

    agent_config = conversation_config.get("agent")

    if not isinstance(agent_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid agent configuration."
        )

    prompt_config = agent_config.get("prompt")

    if not isinstance(prompt_config, dict):
        prompt_config = {}

    asr_config = conversation_config.get("asr")
    turn_config = conversation_config.get("turn")
    tts_config = conversation_config.get("tts")

    if not isinstance(asr_config, dict):
        asr_config = {}

    if not isinstance(turn_config, dict):
        turn_config = {}

    if not isinstance(tts_config, dict):
        tts_config = {}

    tool_ids = prompt_config.get("tool_ids") or []
    knowledge_base = prompt_config.get("knowledge_base") or []
    built_in_tools = prompt_config.get("built_in_tools") or {}
    phone_numbers = payload.get("phone_numbers") or []
    workflow = payload.get("workflow")
    platform_settings = payload.get("platform_settings")
    tags = payload.get("tags") or []

    if not isinstance(tool_ids, list):
        tool_ids = []

    if not isinstance(knowledge_base, list):
        knowledge_base = []

    if not isinstance(built_in_tools, dict):
        built_in_tools = {}

    if not isinstance(phone_numbers, list):
        phone_numbers = []

    if not isinstance(tags, list):
        tags = []

    workflow_keys: list[str] = []
    workflow_node_count = 0
    workflow_edge_count = 0

    if isinstance(workflow, dict):
        workflow_keys = sorted(str(key) for key in workflow.keys())

        workflow_nodes = workflow.get("nodes")
        workflow_edges = workflow.get("edges")

        if isinstance(workflow_nodes, list):
            workflow_node_count = len(workflow_nodes)

        if isinstance(workflow_edges, list):
            workflow_edge_count = len(workflow_edges)

    platform_setting_keys: list[str] = []

    if isinstance(platform_settings, dict):
        platform_setting_keys = sorted(
            str(key) for key in platform_settings.keys()
        )

    return {
        "read_only": True,
        "agent_id": returned_agent_id,
        "name": payload.get("name"),
        "branch_id": payload.get("branch_id"),
        "version_id": payload.get("version_id"),
        "main_branch_id": payload.get("main_branch_id"),
        "phone_number_count": len(phone_numbers),
        "tags": tags,
        "agent": {
            "first_message": agent_config.get("first_message"),
            "language": agent_config.get("language"),
            "disable_first_message_interruptions": (
                agent_config.get(
                    "disable_first_message_interruptions"
                )
            ),
            "dynamic_variables": agent_config.get(
                "dynamic_variables"
            ),
            "prompt": {
                "text": prompt_config.get("prompt"),
                "llm": prompt_config.get("llm"),
                "temperature": prompt_config.get("temperature"),
                "max_tokens": prompt_config.get("max_tokens"),
                "tool_ids": tool_ids,
                "built_in_tool_names": sorted(
                    str(key) for key in built_in_tools.keys()
                ),
                "knowledge_base": knowledge_base,
            },
        },
        "tts": {
            "model_id": tts_config.get("model_id"),
            "voice_id": tts_config.get("voice_id"),
            "stability": tts_config.get("stability"),
            "speed": tts_config.get("speed"),
            "similarity_boost": tts_config.get(
                "similarity_boost"
            ),
        },
        "asr": {
            "provider": asr_config.get("provider"),
            "quality": asr_config.get("quality"),
            "user_input_audio_format": asr_config.get(
                "user_input_audio_format"
            ),
            "keywords": asr_config.get("keywords"),
        },
        "turn": {
            "turn_timeout": turn_config.get("turn_timeout"),
            "initial_wait_time": turn_config.get(
                "initial_wait_time"
            ),
            "silence_end_call_timeout": turn_config.get(
                "silence_end_call_timeout"
            ),
            "turn_eagerness": turn_config.get(
                "turn_eagerness"
            ),
            "mode": turn_config.get("mode"),
        },
        "workflow": {
            "present": isinstance(workflow, dict),
            "keys": workflow_keys,
            "node_count": workflow_node_count,
            "edge_count": workflow_edge_count,
        },
        "platform_settings": {
            "present": isinstance(platform_settings, dict),
            "keys": platform_setting_keys,
        },
    }


def find_agent_by_exact_name(
    agent_name: str,
) -> Optional[dict]:
    """
    Search ElevenLabs for an owned, non-archived agent
    whose name matches exactly.

    Returns None when no exact match exists.

    Raises an error if multiple exact matches exist because
    provisioning cannot safely decide which agent to reuse.
    """

    normalized_name = agent_name.strip()

    if not normalized_name:
        raise ElevenLabsClientError(
            "An agent name is required for agent search."
        )

    query = urlencode(
        {
            "search": normalized_name,
            "page_size": 100,
            "archived": "false",
            "created_by_user_id": "@me",
            "sort_by": "name",
            "sort_direction": "asc",
        }
    )

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents?"
        f"{query}"
    )

    payload = _send_json_request(
        url=url,
        method="GET",
        operation="search agents",
    )

    agents = payload.get("agents")

    if not isinstance(agents, list):
        raise ElevenLabsClientError(
            "ElevenLabs returned an invalid agents list."
        )

    exact_matches: list[dict] = []

    for agent in agents:
        if not isinstance(agent, dict):
            continue

        returned_name = agent.get("name")
        returned_agent_id = agent.get("agent_id")

        if (
            isinstance(returned_name, str)
            and returned_name.strip() == normalized_name
            and isinstance(returned_agent_id, str)
            and returned_agent_id.strip()
        ):
            exact_matches.append(
                {
                    "agent_id": returned_agent_id.strip(),
                    "name": returned_name,
                    "archived": bool(
                        agent.get("archived", False)
                    ),
                    "created_at_unix_secs": agent.get(
                        "created_at_unix_secs"
                    ),
                }
            )

    if len(exact_matches) > 1:
        raise ElevenLabsClientError(
            "Multiple ElevenLabs agents have the exact "
            "provisioning name. Manual review is required."
        )

    if not exact_matches:
        return None

    return exact_matches[0]


def duplicate_template_agent(
    new_agent_name: str,
) -> dict:
    """
    Duplicate the configured ElevenLabs template agent.

    This function creates one new ElevenLabs agent when called.
    It does not connect a phone number or change the template.
    """

    normalized_name = new_agent_name.strip()

    if not normalized_name:
        raise ElevenLabsClientError(
            "A name is required for the duplicated agent."
        )

    template_agent_id = (
        settings.elevenlabs_template_agent_id.strip()
    )

    if not template_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs template agent ID is missing."
        )

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(template_agent_id, safe='')}/duplicate"
    )

    payload = _send_json_request(
        url=url,
        method="POST",
        operation="duplicate template agent",
        body={
            "name": normalized_name,
        },
    )

    duplicated_agent_id = payload.get("agent_id")

    if (
        not isinstance(duplicated_agent_id, str)
        or not duplicated_agent_id.strip()
    ):
        raise ElevenLabsClientError(
            "ElevenLabs did not return the duplicated "
            "agent's agent_id."
        )

    return {
        "success": True,
        "agent_id": duplicated_agent_id.strip(),
        "name": normalized_name,
        "template_agent_id": template_agent_id,
    }

def _build_tool_configuration_snapshot(payload: dict) -> dict:
    """
    Build a safe tool snapshot without returning request-header values.
    """

    tool_id = payload.get("id")
    tool_config = payload.get("tool_config")

    if not isinstance(tool_id, str) or not tool_id.strip():
        raise ElevenLabsClientError(
            "ElevenLabs returned a tool without an id."
        )

    if not isinstance(tool_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned a tool without a valid tool_config."
        )

    api_schema = tool_config.get("api_schema")

    if not isinstance(api_schema, dict):
        api_schema = {}

    request_headers = api_schema.get("request_headers")

    if not isinstance(request_headers, dict):
        request_headers = {}

    access_info = payload.get("access_info")

    if not isinstance(access_info, dict):
        access_info = {}

    usage_stats = payload.get("usage_stats")

    if not isinstance(usage_stats, dict):
        usage_stats = {}

    return {
        "read_only": True,
        "tool_id": tool_id.strip(),
        "name": tool_config.get("name"),
        "type": tool_config.get("type"),
        "description": tool_config.get("description"),
        "response_timeout_secs": tool_config.get(
            "response_timeout_secs"
        ),
        "api_schema": {
            "url": api_schema.get("url"),
            "method": api_schema.get("method"),
            "path_params_schema": api_schema.get(
                "path_params_schema"
            ),
            "query_params_schema": api_schema.get(
                "query_params_schema"
            ),
            "request_body_schema": api_schema.get(
                "request_body_schema"
            ),
            "request_header_names": sorted(
                str(name) for name in request_headers.keys()
            ),
        },
        "dynamic_variables": tool_config.get(
            "dynamic_variables"
        ),
        "access": {
            "is_creator": access_info.get("is_creator"),
            "role": access_info.get("role"),
            "access_source": access_info.get("access_source"),
        },
        "usage": {
            "avg_latency_secs": usage_stats.get(
                "avg_latency_secs"
            ),
            "total_calls": usage_stats.get("total_calls"),
        },
    }


def get_tool_configuration(tool_id: str) -> dict:
    """
    Read one ElevenLabs workspace tool without changing it.

    Request-header names are returned, but their values are never
    included in the snapshot.
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

    snapshot = _build_tool_configuration_snapshot(payload)

    if snapshot["tool_id"] != normalized_tool_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different tool than requested."
        )

    return snapshot


def list_workspace_tools() -> list[dict]:
    """
    List all owned webhook tools in the ElevenLabs workspace.

    The function follows ElevenLabs cursor pagination and returns
    safe summaries without request-header values.
    """

    tools: list[dict] = []
    seen_tool_ids: set[str] = set()
    cursor: Optional[str] = None

    for _ in range(100):
        query_values = {
            "page_size": 100,
            "created_by_user_id": "@me",
            "types": "webhook",
            "sort_by": "name",
            "sort_direction": "asc",
        }

        if cursor:
            query_values["cursor"] = cursor

        query = urlencode(query_values)
        url = (
            f"{ELEVENLABS_API_BASE_URL}/convai/tools?"
            f"{query}"
        )

        payload = _send_json_request(
            url=url,
            method="GET",
            operation="list workspace tools",
        )

        returned_tools = payload.get("tools")

        if not isinstance(returned_tools, list):
            raise ElevenLabsClientError(
                "ElevenLabs returned an invalid tools list."
            )

        for tool in returned_tools:
            if not isinstance(tool, dict):
                continue

            snapshot = _build_tool_configuration_snapshot(tool)
            returned_tool_id = snapshot["tool_id"]

            if returned_tool_id in seen_tool_ids:
                continue

            seen_tool_ids.add(returned_tool_id)
            tools.append(snapshot)

        has_more = payload.get("has_more") is True
        next_cursor = payload.get("next_cursor")

        if not has_more:
            return tools

        if not isinstance(next_cursor, str) or not next_cursor.strip():
            raise ElevenLabsClientError(
                "ElevenLabs reported more tools without a cursor."
            )

        normalized_next_cursor = next_cursor.strip()

        if normalized_next_cursor == cursor:
            raise ElevenLabsClientError(
                "ElevenLabs repeated the same tools cursor."
            )

        cursor = normalized_next_cursor

    raise ElevenLabsClientError(
        "ElevenLabs tools pagination exceeded the safety limit."
    )


def find_tool_by_exact_name(
    tool_name: str,
) -> Optional[dict]:
    """
    Find one owned webhook tool whose name matches exactly.

    Returns None when no exact match exists. Raises an error if
    multiple exact matches exist so provisioning never chooses an
    ambiguous tool.
    """

    normalized_name = tool_name.strip()

    if not normalized_name:
        raise ElevenLabsClientError(
            "A tool name is required for tool search."
        )

    exact_matches: list[dict] = []
    seen_tool_ids: set[str] = set()
    cursor: Optional[str] = None

    for _ in range(100):
        query_values = {
            "search": normalized_name,
            "page_size": 100,
            "created_by_user_id": "@me",
            "types": "webhook",
            "sort_by": "name",
            "sort_direction": "asc",
        }

        if cursor:
            query_values["cursor"] = cursor

        query = urlencode(query_values)
        url = (
            f"{ELEVENLABS_API_BASE_URL}/convai/tools?"
            f"{query}"
        )

        payload = _send_json_request(
            url=url,
            method="GET",
            operation="search workspace tools",
        )

        returned_tools = payload.get("tools")

        if not isinstance(returned_tools, list):
            raise ElevenLabsClientError(
                "ElevenLabs returned an invalid tools list."
            )

        for tool in returned_tools:
            if not isinstance(tool, dict):
                continue

            snapshot = _build_tool_configuration_snapshot(tool)
            returned_name = snapshot.get("name")

            returned_tool_id = snapshot["tool_id"]

            if returned_tool_id in seen_tool_ids:
                continue

            seen_tool_ids.add(returned_tool_id)

            if (
                isinstance(returned_name, str)
                and returned_name.strip() == normalized_name
            ):
                exact_matches.append(snapshot)

        has_more = payload.get("has_more") is True
        next_cursor = payload.get("next_cursor")

        if not has_more:
            break

        if not isinstance(next_cursor, str) or not next_cursor.strip():
            raise ElevenLabsClientError(
                "ElevenLabs reported more tools without a cursor."
            )

        normalized_next_cursor = next_cursor.strip()

        if normalized_next_cursor == cursor:
            raise ElevenLabsClientError(
                "ElevenLabs repeated the same tools cursor."
            )

        cursor = normalized_next_cursor

    else:
        raise ElevenLabsClientError(
            "ElevenLabs tool search pagination exceeded the "
            "safety limit."
        )

    if len(exact_matches) > 1:
        raise ElevenLabsClientError(
            "Multiple ElevenLabs tools have the exact provisioning "
            "name. Manual review is required."
        )

    if not exact_matches:
        return None

    return exact_matches[0]


def create_webhook_tool(tool_config: dict) -> dict:
    """
    Create one secure ElevenLabs webhook tool.

    This function does not connect the created tool to an agent.
    Svir v2 tools must use HTTPS, POST, a JSON body schema, and the
    X-Svir-Tool-Token request header.
    """

    if not isinstance(tool_config, dict):
        raise ElevenLabsClientError(
            "A valid webhook tool_config is required."
        )

    try:
        normalized_config = json.loads(
            json.dumps(tool_config)
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ElevenLabsClientError(
            "Webhook tool_config must be JSON serializable."
        ) from exc

    if normalized_config.get("type") != "webhook":
        raise ElevenLabsClientError(
            "Only ElevenLabs webhook tools can be created here."
        )

    tool_name = normalized_config.get("name")

    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ElevenLabsClientError(
            "The webhook tool name is missing."
        )

    normalized_name = tool_name.strip()
    normalized_config["name"] = normalized_name

    api_schema = normalized_config.get("api_schema")

    if not isinstance(api_schema, dict):
        raise ElevenLabsClientError(
            "The webhook tool api_schema is missing."
        )

    webhook_url = api_schema.get("url")

    if (
        not isinstance(webhook_url, str)
        or not webhook_url.strip().lower().startswith("https://")
    ):
        raise ElevenLabsClientError(
            "The webhook tool must use an HTTPS URL."
        )

    api_schema["url"] = webhook_url.strip()

    method = api_schema.get("method")

    if not isinstance(method, str) or method.strip().upper() != "POST":
        raise ElevenLabsClientError(
            "Svir v2 webhook tools must use POST."
        )

    api_schema["method"] = "POST"

    request_body_schema = api_schema.get("request_body_schema")

    if not isinstance(request_body_schema, dict):
        raise ElevenLabsClientError(
            "The webhook tool request_body_schema is missing."
        )

    request_headers = api_schema.get("request_headers")

    if not isinstance(request_headers, dict):
        raise ElevenLabsClientError(
            "The webhook tool request_headers are missing."
        )

    has_tool_token_header = any(
        str(header_name).strip().lower()
        == "x-svir-tool-token"
        for header_name in request_headers.keys()
    )

    if not has_tool_token_header:
        raise ElevenLabsClientError(
            "The webhook tool is missing X-Svir-Tool-Token."
        )

    url = f"{ELEVENLABS_API_BASE_URL}/convai/tools"

    payload = _send_json_request(
        url=url,
        method="POST",
        operation="create webhook tool",
        body={
            "tool_config": normalized_config,
        },
    )

    snapshot = _build_tool_configuration_snapshot(payload)

    if snapshot.get("name") != normalized_name:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different tool name after creation."
        )

    if snapshot.get("type") != "webhook":
        raise ElevenLabsClientError(
            "ElevenLabs returned a non-webhook tool after creation."
        )

    return {
        "success": True,
        **snapshot,
    }

def _normalize_agent_attachment_identifier(
    value: object,
    *,
    field_name: str,
    required_prefix: str,
) -> str:
    """
    Normalize one ElevenLabs identifier used by the controlled
    agent-tool attachment client.
    """

    if not isinstance(value, str):
        raise ElevenLabsClientError(
            f"{field_name} must be a string."
        )

    normalized_value = value.strip()

    if not normalized_value:
        raise ElevenLabsClientError(
            f"{field_name} is missing."
        )

    if not normalized_value.startswith(required_prefix):
        raise ElevenLabsClientError(
            f"{field_name} has an unexpected format."
        )

    return normalized_value


def _normalize_agent_attachment_tool_ids(
    values: object,
    *,
    field_name: str,
    require_exactly_five: bool,
) -> list[str]:
    """
    Normalize a deterministic list of ElevenLabs workspace tool IDs.
    """

    if not isinstance(values, list):
        raise ElevenLabsClientError(
            f"{field_name} must be a list."
        )

    normalized_values: list[str] = []

    for value in values:
        normalized_values.append(
            _normalize_agent_attachment_identifier(
                value,
                field_name=f"{field_name} item",
                required_prefix="tool_",
            )
        )

    if len(normalized_values) != len(set(normalized_values)):
        raise ElevenLabsClientError(
            f"{field_name} contains duplicate tool IDs."
        )

    if require_exactly_five and len(normalized_values) != 5:
        raise ElevenLabsClientError(
            f"{field_name} must contain exactly five tool IDs."
        )

    return normalized_values


def _read_agent_branch_payload(
    *,
    agent_id: str,
    branch_id: str,
    operation: str,
) -> dict:
    """
    Read exactly one ElevenLabs agent branch without modifying it.
    """

    query = urlencode(
        {
            "branch_id": branch_id,
        }
    )
    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(agent_id, safe='')}?{query}"
    )

    payload = _send_json_request(
        url=url,
        method="GET",
        operation=operation,
    )

    returned_agent_id = payload.get("agent_id")
    returned_branch_id = payload.get("branch_id")

    if returned_agent_id != agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different agent than requested."
        )

    if returned_branch_id != branch_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different branch than requested."
        )

    return payload


def _extract_agent_prompt_tool_ids(
    payload: dict,
) -> list[str]:
    """
    Read and validate prompt.tool_ids from one full agent payload.
    """

    conversation_config = payload.get("conversation_config")

    if not isinstance(conversation_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid conversation_config."
        )

    agent_config = conversation_config.get("agent")

    if not isinstance(agent_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid agent configuration."
        )

    prompt_config = agent_config.get("prompt")

    if not isinstance(prompt_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid prompt configuration."
        )

    raw_tool_ids = prompt_config.get("tool_ids")

    if raw_tool_ids is None:
        raw_tool_ids = []

    return _normalize_agent_attachment_tool_ids(
        raw_tool_ids,
        field_name="current prompt.tool_ids",
        require_exactly_five=False,
    )


def _build_agent_state_without_prompt_tool_ids(
    payload: dict,
) -> dict:
    """
    Build a deep JSON-safe comparison snapshot while excluding only
    prompt.tool_ids and fields expected to change after a draft edit.
    """

    try:
        normalized_payload = json.loads(
            json.dumps(payload)
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ElevenLabsClientError(
            "ElevenLabs returned a non-serializable agent payload."
        ) from exc

    conversation_config = normalized_payload.get(
        "conversation_config"
    )

    if not isinstance(conversation_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid conversation_config."
        )

    agent_config = conversation_config.get("agent")

    if not isinstance(agent_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid agent configuration."
        )

    prompt_config = agent_config.get("prompt")

    if not isinstance(prompt_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid prompt configuration."
        )

    prompt_config.pop("tool_ids", None)
    # ElevenLabs regenerates this expanded view when tool_ids changes.
    prompt_config.pop("tools", None)

    return {
        "agent_id": normalized_payload.get("agent_id"),
        "name": normalized_payload.get("name"),
        "branch_id": normalized_payload.get("branch_id"),
        "main_branch_id": normalized_payload.get(
            "main_branch_id"
        ),
        "conversation_config": conversation_config,
        "platform_settings": normalized_payload.get(
            "platform_settings"
        ),
        "workflow": normalized_payload.get("workflow"),
        "phone_numbers": normalized_payload.get(
            "phone_numbers"
        ),
        "whatsapp_accounts": normalized_payload.get(
            "whatsapp_accounts"
        ),
        "tags": normalized_payload.get("tags"),
    }


def attach_agent_prompt_tool_ids(
    *,
    agent_id: str,
    branch_id: str,
    tool_ids: list[str],
    expected_current_tool_ids: list[str],
    require_exactly_five: bool = True,
) -> dict:
    """
    Attach an exact workspace-tool list to one explicit agent branch.

    Safety properties:
    - Reads the exact branch before writing.
    - Is idempotent when the exact desired tool list already exists.
    - Refuses to overwrite an unexpected current tool list.
    - Sends a PATCH body containing only
      conversation_config.agent.prompt.tool_ids.
    - Reads the branch again and verifies that all other inspected
      agent state remained unchanged.
    - Does not publish, deploy, connect a phone number, update a
      prompt, change a knowledge base, or write to Supabase.
    """

    normalized_agent_id = (
        _normalize_agent_attachment_identifier(
            agent_id,
            field_name="agent_id",
            required_prefix="agent_",
        )
    )
    normalized_branch_id = (
        _normalize_agent_attachment_identifier(
            branch_id,
            field_name="branch_id",
            required_prefix="agtbrch_",
        )
    )
    normalized_tool_ids = (
        _normalize_agent_attachment_tool_ids(
            tool_ids,
            field_name="tool_ids",
            require_exactly_five=require_exactly_five,
        )
    )
    normalized_expected_tool_ids = (
        _normalize_agent_attachment_tool_ids(
            expected_current_tool_ids,
            field_name="expected_current_tool_ids",
            require_exactly_five=False,
        )
    )

    before_payload = _read_agent_branch_payload(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        operation="read agent branch before tool attachment",
    )
    current_tool_ids = _extract_agent_prompt_tool_ids(
        before_payload
    )

    if current_tool_ids == normalized_tool_ids:
        return {
            "success": True,
            "agent_id": normalized_agent_id,
            "branch_id": normalized_branch_id,
            "version_id": before_payload.get("version_id"),
            "tool_ids": normalized_tool_ids,
            "attached_new_tools": False,
            "reused_existing_attachment": True,
            "changed_fields": [],
        }

    if current_tool_ids != normalized_expected_tool_ids:
        raise ElevenLabsClientError(
            "The agent branch has an unexpected current tool list. "
            "No attachment was attempted."
        )

    before_state = (
        _build_agent_state_without_prompt_tool_ids(
            before_payload
        )
    )

    query = urlencode(
        {
            "branch_id": normalized_branch_id,
        }
    )
    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(normalized_agent_id, safe='')}?{query}"
    )
    request_body = {
        "conversation_config": {
            "agent": {
                "prompt": {
                    "tool_ids": normalized_tool_ids,
                },
            },
        },
    }

    updated_payload = _send_json_request(
        url=url,
        method="PATCH",
        operation="attach tools to agent branch",
        body=request_body,
    )

    if updated_payload.get("agent_id") != normalized_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different agent after "
            "tool attachment."
        )

    if updated_payload.get("branch_id") != normalized_branch_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different branch after "
            "tool attachment."
        )

    returned_tool_ids = _extract_agent_prompt_tool_ids(
        updated_payload
    )

    if returned_tool_ids != normalized_tool_ids:
        raise ElevenLabsClientError(
            "ElevenLabs did not return the exact requested tool list."
        )

    after_payload = _read_agent_branch_payload(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        operation="verify agent branch after tool attachment",
    )
    verified_tool_ids = _extract_agent_prompt_tool_ids(
        after_payload
    )

    if verified_tool_ids != normalized_tool_ids:
        raise ElevenLabsClientError(
            "The agent branch does not contain the exact requested "
            "tool list after attachment."
        )

    after_state = (
        _build_agent_state_without_prompt_tool_ids(
            after_payload
        )
    )

    if after_state != before_state:
        raise ElevenLabsClientError(
            "Unexpected agent fields changed during tool attachment."
        )

    return {
        "success": True,
        "agent_id": normalized_agent_id,
        "branch_id": normalized_branch_id,
        "version_id": after_payload.get("version_id"),
        "tool_ids": normalized_tool_ids,
        "attached_new_tools": True,
        "reused_existing_attachment": False,
        "changed_fields": [
            "conversation_config.agent.prompt.tool_ids",
        ],
    }


def _build_main_branch_guard_state(payload: dict) -> dict:
    """Capture every Main field relevant to runtime behavior."""

    return {
        "agent_id": payload.get("agent_id"),
        "branch_id": payload.get("branch_id"),
        "main_branch_id": payload.get("main_branch_id"),
        "version_id": payload.get("version_id"),
        "conversation_config": payload.get("conversation_config"),
        "platform_settings": payload.get("platform_settings"),
        "workflow": payload.get("workflow"),
        "phone_numbers": payload.get("phone_numbers"),
        "whatsapp_accounts": payload.get("whatsapp_accounts"),
        "tags": payload.get("tags"),
    }


def _read_and_validate_test_branch_pair(
    *,
    agent_id: str,
    branch_id: str,
    main_branch_id: str,
    operation_prefix: str,
) -> tuple[dict, dict]:
    """Read a Main/test pair and fail closed on branch confusion."""

    if branch_id == main_branch_id:
        raise ElevenLabsClientError(
            "The requested test branch is Main. No update was attempted."
        )

    main_payload = _read_agent_branch_payload(
        agent_id=agent_id,
        branch_id=main_branch_id,
        operation=f"{operation_prefix}: read protected Main branch",
    )
    test_payload = _read_agent_branch_payload(
        agent_id=agent_id,
        branch_id=branch_id,
        operation=f"{operation_prefix}: read test branch",
    )

    if test_payload.get("main_branch_id") != main_branch_id:
        raise ElevenLabsClientError(
            "The requested branch does not belong to the protected Main "
            "branch. No update was attempted."
        )
    if main_payload.get("main_branch_id") != main_branch_id:
        raise ElevenLabsClientError(
            "The protected branch is not reported as Main. No update was "
            "attempted."
        )

    return main_payload, test_payload


def replace_test_branch_prompt_tool_ids(
    *,
    agent_id: str,
    branch_id: str,
    main_branch_id: str,
    tool_ids: list[str],
    expected_current_tool_ids: list[str],
) -> dict:
    """Replace tools on a verified non-Main branch and guard Main."""

    normalized_agent_id = _normalize_agent_attachment_identifier(
        agent_id,
        field_name="agent_id",
        required_prefix="agent_",
    )
    normalized_branch_id = _normalize_agent_attachment_identifier(
        branch_id,
        field_name="branch_id",
        required_prefix="agtbrch_",
    )
    normalized_main_branch_id = _normalize_agent_attachment_identifier(
        main_branch_id,
        field_name="main_branch_id",
        required_prefix="agtbrch_",
    )
    normalized_tool_ids = _normalize_agent_attachment_tool_ids(
        tool_ids,
        field_name="tool_ids",
        require_exactly_five=False,
    )
    if not normalized_tool_ids:
        raise ElevenLabsClientError(
            "A test branch must retain at least one workspace tool."
        )

    main_before, _ = _read_and_validate_test_branch_pair(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        main_branch_id=normalized_main_branch_id,
        operation_prefix="replace test branch tools",
    )
    main_guard = _build_main_branch_guard_state(main_before)

    result = attach_agent_prompt_tool_ids(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        tool_ids=normalized_tool_ids,
        expected_current_tool_ids=expected_current_tool_ids,
        require_exactly_five=False,
    )

    main_after = _read_agent_branch_payload(
        agent_id=normalized_agent_id,
        branch_id=normalized_main_branch_id,
        operation="verify protected Main after test tool replacement",
    )
    if _build_main_branch_guard_state(main_after) != main_guard:
        raise ElevenLabsClientError(
            "Protected Main changed while updating the test branch."
        )

    return result


def _extract_agent_prompt_text(
    payload: dict,
) -> str:
    """
    Read the full prompt text from one ElevenLabs agent payload.
    """

    conversation_config = payload.get("conversation_config")

    if not isinstance(conversation_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid conversation_config."
        )

    agent_config = conversation_config.get("agent")

    if not isinstance(agent_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid agent configuration."
        )

    prompt_config = agent_config.get("prompt")

    if not isinstance(prompt_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid prompt configuration."
        )

    prompt_text = prompt_config.get("prompt")

    if not isinstance(prompt_text, str):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid prompt text."
        )

    return prompt_text


def _extract_agent_knowledge_base_document_ids(
    payload: dict,
) -> list[str]:
    """
    Read deterministic knowledge-base document IDs from one agent.
    """

    conversation_config = payload.get("conversation_config")

    if not isinstance(conversation_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid conversation_config."
        )

    agent_config = conversation_config.get("agent")

    if not isinstance(agent_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid agent configuration."
        )

    prompt_config = agent_config.get("prompt")

    if not isinstance(prompt_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid prompt configuration."
        )

    knowledge_base = prompt_config.get("knowledge_base")

    if not isinstance(knowledge_base, list):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid knowledge-base list."
        )

    document_ids: list[str] = []

    for entry in knowledge_base:
        if not isinstance(entry, dict):
            raise ElevenLabsClientError(
                "ElevenLabs returned an invalid knowledge-base entry."
            )

        document_id = entry.get("id")

        if not isinstance(document_id, str):
            raise ElevenLabsClientError(
                "A knowledge-base entry has no valid document ID."
            )

        normalized_document_id = document_id.strip()

        if not normalized_document_id:
            raise ElevenLabsClientError(
                "A knowledge-base entry has an empty document ID."
            )

        document_ids.append(normalized_document_id)

    if len(document_ids) != len(set(document_ids)):
        raise ElevenLabsClientError(
            "The agent contains duplicate knowledge-base document IDs."
        )

    return document_ids


def _normalize_prompt_update_document_ids(
    values: object,
    *,
    field_name: str,
) -> list[str]:
    """
    Normalize an exact expected knowledge-base document ID list.
    """

    if not isinstance(values, list):
        raise ElevenLabsClientError(
            f"{field_name} must be a list."
        )

    normalized_values: list[str] = []

    for value in values:
        if not isinstance(value, str):
            raise ElevenLabsClientError(
                f"{field_name} items must be strings."
            )

        normalized_value = value.strip()

        if not normalized_value:
            raise ElevenLabsClientError(
                f"{field_name} contains an empty document ID."
            )

        normalized_values.append(normalized_value)

    if len(normalized_values) != len(set(normalized_values)):
        raise ElevenLabsClientError(
            f"{field_name} contains duplicate document IDs."
        )

    if not normalized_values:
        raise ElevenLabsClientError(
            f"{field_name} must contain at least one document ID."
        )

    return normalized_values


def _normalize_expected_prompt_sha256(
    value: object,
) -> str:
    """
    Normalize one lowercase SHA-256 prompt precondition.
    """

    if not isinstance(value, str):
        raise ElevenLabsClientError(
            "expected_current_prompt_sha256 must be a string."
        )

    normalized_value = value.strip().lower()

    if (
        len(normalized_value) != 64
        or any(
            character not in "0123456789abcdef"
            for character in normalized_value
        )
    ):
        raise ElevenLabsClientError(
            "expected_current_prompt_sha256 is invalid."
        )

    return normalized_value


def _build_prompt_update_protected_state(
    payload: dict,
) -> dict:
    """
    Build a stable snapshot of fields that a prompt-only update must
    not change.

    Automatic metadata, version IDs and platform-generated state are
    intentionally excluded. The prompt text itself is also excluded.
    """

    try:
        normalized_payload = json.loads(
            json.dumps(payload)
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ElevenLabsClientError(
            "ElevenLabs returned a non-serializable agent payload."
        ) from exc

    conversation_config = normalized_payload.get(
        "conversation_config"
    )

    if not isinstance(conversation_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid conversation_config."
        )

    agent_config = conversation_config.get("agent")

    if not isinstance(agent_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid agent configuration."
        )

    prompt_config = agent_config.get("prompt")

    if not isinstance(prompt_config, dict):
        raise ElevenLabsClientError(
            "ElevenLabs returned no valid prompt configuration."
        )

    protected_prompt = dict(prompt_config)
    protected_prompt.pop("prompt", None)

    return {
        "agent_id": normalized_payload.get("agent_id"),
        "name": normalized_payload.get("name"),
        "branch_id": normalized_payload.get("branch_id"),
        "main_branch_id": normalized_payload.get(
            "main_branch_id"
        ),
        "phone_numbers": normalized_payload.get(
            "phone_numbers"
        ),
        "tags": normalized_payload.get("tags"),
        "agent": {
            "first_message": agent_config.get("first_message"),
            "language": agent_config.get("language"),
            "disable_first_message_interruptions": (
                agent_config.get(
                    "disable_first_message_interruptions"
                )
            ),
            "dynamic_variables": agent_config.get(
                "dynamic_variables"
            ),
            "prompt": protected_prompt,
        },
        "tts": conversation_config.get("tts"),
        "asr": conversation_config.get("asr"),
        "turn": conversation_config.get("turn"),
        "workflow": normalized_payload.get("workflow"),
    }


def update_agent_prompt_text(
    *,
    agent_id: str,
    branch_id: str,
    prompt_text: str,
    expected_current_prompt_sha256: str,
    required_tool_ids: list[str],
    required_knowledge_base_document_ids: list[str],
    require_exactly_five_tools: bool = True,
) -> dict:
    """
    Update only the prompt text on one explicit ElevenLabs branch.

    The exact current prompt hash, five tool IDs and knowledge-base
    document IDs are required before any PATCH is attempted.
    """

    normalized_agent_id = (
        _normalize_agent_attachment_identifier(
            agent_id,
            field_name="agent_id",
            required_prefix="agent_",
        )
    )
    normalized_branch_id = (
        _normalize_agent_attachment_identifier(
            branch_id,
            field_name="branch_id",
            required_prefix="agtbrch_",
        )
    )

    if not isinstance(prompt_text, str):
        raise ElevenLabsClientError(
            "prompt_text must be a string."
        )

    normalized_prompt_text = prompt_text.strip()

    if not normalized_prompt_text:
        raise ElevenLabsClientError(
            "prompt_text must not be empty."
        )

    normalized_expected_sha256 = (
        _normalize_expected_prompt_sha256(
            expected_current_prompt_sha256
        )
    )
    normalized_required_tool_ids = (
        _normalize_agent_attachment_tool_ids(
            required_tool_ids,
            field_name="required_tool_ids",
            require_exactly_five=require_exactly_five_tools,
        )
    )
    normalized_required_document_ids = (
        _normalize_prompt_update_document_ids(
            required_knowledge_base_document_ids,
            field_name=(
                "required_knowledge_base_document_ids"
            ),
        )
    )

    before_payload = _read_agent_branch_payload(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        operation="read agent branch before prompt update",
    )
    current_prompt_text = _extract_agent_prompt_text(
        before_payload
    )
    current_prompt_sha256 = hashlib.sha256(
        current_prompt_text.encode("utf-8")
    ).hexdigest()

    if current_prompt_sha256 != normalized_expected_sha256:
        raise ElevenLabsClientError(
            "The current agent prompt does not match the approved "
            "prompt precondition. No update was attempted."
        )

    current_tool_ids = _extract_agent_prompt_tool_ids(
        before_payload
    )

    if current_tool_ids != normalized_required_tool_ids:
        raise ElevenLabsClientError(
            "The agent does not contain the exact required tool list. "
            "No prompt update was attempted."
        )

    current_document_ids = (
        _extract_agent_knowledge_base_document_ids(
            before_payload
        )
    )

    if current_document_ids != normalized_required_document_ids:
        raise ElevenLabsClientError(
            "The agent does not contain the exact required "
            "knowledge-base document list. No prompt update was "
            "attempted."
        )

    if current_prompt_text == normalized_prompt_text:
        return {
            "success": True,
            "agent_id": normalized_agent_id,
            "branch_id": normalized_branch_id,
            "version_id": before_payload.get("version_id"),
            "prompt_sha256": current_prompt_sha256,
            "updated_prompt_text": False,
            "reused_existing_prompt": True,
            "changed_fields": [],
        }

    before_protected_state = (
        _build_prompt_update_protected_state(
            before_payload
        )
    )

    query = urlencode(
        {
            "branch_id": normalized_branch_id,
        }
    )
    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(normalized_agent_id, safe='')}?{query}"
    )
    request_body = {
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": normalized_prompt_text,
                },
            },
        },
    }

    updated_payload = _send_json_request(
        url=url,
        method="PATCH",
        operation="update agent prompt text",
        body=request_body,
    )

    if updated_payload.get("agent_id") != normalized_agent_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different agent after "
            "the prompt update."
        )

    if updated_payload.get("branch_id") != normalized_branch_id:
        raise ElevenLabsClientError(
            "ElevenLabs returned a different branch after "
            "the prompt update."
        )

    returned_prompt_text = _extract_agent_prompt_text(
        updated_payload
    )

    if returned_prompt_text != normalized_prompt_text:
        raise ElevenLabsClientError(
            "ElevenLabs did not return the exact requested "
            "prompt text."
        )

    after_payload = _read_agent_branch_payload(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        operation="verify agent branch after prompt update",
    )

    if (
        _extract_agent_prompt_text(after_payload)
        != normalized_prompt_text
    ):
        raise ElevenLabsClientError(
            "The agent branch does not contain the exact requested "
            "prompt text after the update."
        )

    if (
        _extract_agent_prompt_tool_ids(after_payload)
        != normalized_required_tool_ids
    ):
        raise ElevenLabsClientError(
            "The agent tool list changed during the prompt update."
        )

    if (
        _extract_agent_knowledge_base_document_ids(
            after_payload
        )
        != normalized_required_document_ids
    ):
        raise ElevenLabsClientError(
            "The agent knowledge-base list changed during "
            "the prompt update."
        )

    after_protected_state = (
        _build_prompt_update_protected_state(
            after_payload
        )
    )

    if after_protected_state != before_protected_state:
        raise ElevenLabsClientError(
            "Unexpected protected agent fields changed during "
            "the prompt update."
        )

    prompt_sha256 = hashlib.sha256(
        normalized_prompt_text.encode("utf-8")
    ).hexdigest()

    return {
        "success": True,
        "agent_id": normalized_agent_id,
        "branch_id": normalized_branch_id,
        "version_id": after_payload.get("version_id"),
        "prompt_sha256": prompt_sha256,
        "updated_prompt_text": True,
        "reused_existing_prompt": False,
        "changed_fields": [
            "conversation_config.agent.prompt.prompt",
        ],
    }


def update_test_branch_prompt_text(
    *,
    agent_id: str,
    branch_id: str,
    main_branch_id: str,
    prompt_text: str,
    expected_current_prompt_sha256: str,
    required_tool_ids: list[str],
    required_knowledge_base_document_ids: list[str],
) -> dict:
    """Update prompt text on a verified non-Main branch and guard Main."""

    normalized_agent_id = _normalize_agent_attachment_identifier(
        agent_id,
        field_name="agent_id",
        required_prefix="agent_",
    )
    normalized_branch_id = _normalize_agent_attachment_identifier(
        branch_id,
        field_name="branch_id",
        required_prefix="agtbrch_",
    )
    normalized_main_branch_id = _normalize_agent_attachment_identifier(
        main_branch_id,
        field_name="main_branch_id",
        required_prefix="agtbrch_",
    )

    main_before, _ = _read_and_validate_test_branch_pair(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        main_branch_id=normalized_main_branch_id,
        operation_prefix="update test branch prompt",
    )
    main_guard = _build_main_branch_guard_state(main_before)

    result = update_agent_prompt_text(
        agent_id=normalized_agent_id,
        branch_id=normalized_branch_id,
        prompt_text=prompt_text,
        expected_current_prompt_sha256=expected_current_prompt_sha256,
        required_tool_ids=required_tool_ids,
        required_knowledge_base_document_ids=(
            required_knowledge_base_document_ids
        ),
        require_exactly_five_tools=False,
    )

    main_after = _read_agent_branch_payload(
        agent_id=normalized_agent_id,
        branch_id=normalized_main_branch_id,
        operation="verify protected Main after test prompt update",
    )
    if _build_main_branch_guard_state(main_after) != main_guard:
        raise ElevenLabsClientError(
            "Protected Main changed while updating the test branch."
        )

    return result
