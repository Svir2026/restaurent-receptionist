from __future__ import annotations

from app.services.elevenlabs_client import (
    ElevenLabsClientError,
    update_agent_prompt_text,
)
from app.services.yz_agent_active_prompt import (
    get_yz_active_prompt_definition,
)


YZ_AGENT_ID = "agent_3701kycttzk2e3babhgdksfcjh9g"
YZ_BRANCH_ID = "agtbrch_5501kycttzkmf9ksz96y5mbzpj3f"

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

YZ_EXPECTED_CURRENT_PROMPT_SHA256 = (
    "03defb19ee25767b869ed1bb43fe148a01c36e212b8bb052b6d6e17d40bf760b"
)

YZ_ACTIVE_PROMPT_SHA256 = (
    "4d3ed714511ab3853cc51f360dbabfabc0f26b979f767bd6c66f44a6fed153e0"
)


class YZAgentPromptConnectorError(RuntimeError):
    """
    Raised when the controlled YZ active-prompt connection is blocked.
    """


def _require_exact_prompt_definition(
    definition: object,
) -> dict:
    """
    Validate the reviewed prompt definition before any write attempt.
    """

    if not isinstance(definition, dict):
        raise YZAgentPromptConnectorError(
            "The YZ active prompt definition is invalid."
        )

    if definition.get("agent_id") != YZ_AGENT_ID:
        raise YZAgentPromptConnectorError(
            "The prompt definition targets an unexpected agent."
        )

    if definition.get("branch_id") != YZ_BRANCH_ID:
        raise YZAgentPromptConnectorError(
            "The prompt definition targets an unexpected branch."
        )

    if (
        definition.get("expected_current_prompt_sha256")
        != YZ_EXPECTED_CURRENT_PROMPT_SHA256
    ):
        raise YZAgentPromptConnectorError(
            "The prompt definition has an unexpected current-prompt "
            "precondition."
        )

    if (
        definition.get("active_prompt_sha256")
        != YZ_ACTIVE_PROMPT_SHA256
    ):
        raise YZAgentPromptConnectorError(
            "The prompt definition has an unexpected active-prompt hash."
        )

    if definition.get("required_tool_ids") != list(
        YZ_REQUIRED_TOOL_IDS
    ):
        raise YZAgentPromptConnectorError(
            "The prompt definition does not require the exact five "
            "approved YZ tools."
        )

    if (
        definition.get(
            "required_knowledge_base_document_ids"
        )
        != list(YZ_REQUIRED_KNOWLEDGE_BASE_DOCUMENT_IDS)
    ):
        raise YZAgentPromptConnectorError(
            "The prompt definition does not require the exact approved "
            "YZ knowledge base."
        )

    prompt_text = definition.get("prompt_text")

    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise YZAgentPromptConnectorError(
            "The reviewed active prompt text is missing."
        )

    return definition


def connect_yz_agent_active_prompt() -> dict:
    """
    Apply the reviewed active prompt to exactly the approved YZ branch.

    This function accepts no IDs, prompt text, tool IDs, knowledge-base
    IDs, or hashes from the caller. All safety-critical values are
    locked in this module and independently verified against the
    reviewed prompt definition.

    Importing or deploying this module does not call ElevenLabs.
    """

    definition = _require_exact_prompt_definition(
        get_yz_active_prompt_definition()
    )

    try:
        result = update_agent_prompt_text(
            agent_id=YZ_AGENT_ID,
            branch_id=YZ_BRANCH_ID,
            prompt_text=definition["prompt_text"],
            expected_current_prompt_sha256=(
                YZ_EXPECTED_CURRENT_PROMPT_SHA256
            ),
            required_tool_ids=list(YZ_REQUIRED_TOOL_IDS),
            required_knowledge_base_document_ids=list(
                YZ_REQUIRED_KNOWLEDGE_BASE_DOCUMENT_IDS
            ),
        )
    except ElevenLabsClientError as error:
        raise YZAgentPromptConnectorError(
            "YZ active prompt connection was safely blocked: "
            f"{error}"
        ) from error

    if not isinstance(result, dict):
        raise YZAgentPromptConnectorError(
            "The prompt update client returned an invalid result."
        )

    if result.get("success") is not True:
        raise YZAgentPromptConnectorError(
            "The prompt update client did not report success."
        )

    if result.get("agent_id") != YZ_AGENT_ID:
        raise YZAgentPromptConnectorError(
            "The prompt update returned an unexpected agent."
        )

    if result.get("branch_id") != YZ_BRANCH_ID:
        raise YZAgentPromptConnectorError(
            "The prompt update returned an unexpected branch."
        )

    if result.get("prompt_sha256") != YZ_ACTIVE_PROMPT_SHA256:
        raise YZAgentPromptConnectorError(
            "The prompt update returned an unexpected prompt hash."
        )

    updated_prompt_text = result.get("updated_prompt_text")
    reused_existing_prompt = result.get("reused_existing_prompt")
    changed_fields = result.get("changed_fields")

    if updated_prompt_text is True:
        if reused_existing_prompt is not False:
            raise YZAgentPromptConnectorError(
                "The prompt update returned inconsistent write flags."
            )

        if changed_fields != [
            "conversation_config.agent.prompt.prompt"
        ]:
            raise YZAgentPromptConnectorError(
                "The prompt update reported unexpected changed fields."
            )

    elif reused_existing_prompt is True:
        if updated_prompt_text is not False:
            raise YZAgentPromptConnectorError(
                "The prompt update returned inconsistent reuse flags."
            )

        if changed_fields != []:
            raise YZAgentPromptConnectorError(
                "The reused prompt result reported changed fields."
            )

    else:
        raise YZAgentPromptConnectorError(
            "The prompt update returned an unknown result state."
        )

    return {
        "success": True,
        "agent_id": YZ_AGENT_ID,
        "branch_id": YZ_BRANCH_ID,
        "version_id": result.get("version_id"),
        "prompt_sha256": YZ_ACTIVE_PROMPT_SHA256,
        "updated_prompt_text": updated_prompt_text,
        "reused_existing_prompt": reused_existing_prompt,
        "changed_fields": changed_fields,
        "tool_ids_preserved": list(YZ_REQUIRED_TOOL_IDS),
        "knowledge_base_document_ids_preserved": list(
            YZ_REQUIRED_KNOWLEDGE_BASE_DOCUMENT_IDS
        ),
        "published": False,
        "phone_connection_changed": False,
        "supabase_changed": False,
        "provisioning_step_advanced": False,
    }
