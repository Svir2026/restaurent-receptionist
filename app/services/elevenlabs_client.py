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
