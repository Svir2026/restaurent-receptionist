import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.core.config import settings


ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsClientError(RuntimeError):
    """Raised when communication with ElevenLabs fails."""


def get_template_agent_summary() -> dict:
    """Read the configured template agent without changing anything."""

    agent_id = settings.elevenlabs_template_agent_id.strip()
    api_key = settings.elevenlabs_api_key.get_secret_value()

    url = (
        f"{ELEVENLABS_API_BASE_URL}/convai/agents/"
        f"{quote(agent_id, safe='')}"
    )

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "xi-api-key": api_key,
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ElevenLabsClientError(
            f"ElevenLabs returned HTTP {exc.code}: {detail[:300]}"
        ) from exc

    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ElevenLabsClientError(
            "Could not read the ElevenLabs template agent."
        ) from exc

    if not isinstance(payload, dict) or not payload.get("agent_id"):
        raise ElevenLabsClientError(
            "ElevenLabs returned an unexpected agent response."
        )

    phone_numbers = payload.get("phone_numbers") or []

    return {
        "agent_id": payload.get("agent_id"),
        "name": payload.get("name"),
        "phone_number_count": len(phone_numbers),
        "branch_id": payload.get("branch_id"),
        "version_id": payload.get("version_id"),
    }
