import json
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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
