from __future__ import annotations

from app.services.elevenlabs_client import (
    ElevenLabsClientError,
    attach_agent_prompt_tool_ids,
)


YZ_AGENT_ID = "agent_3701kycttzk2e3babhgdksfcjh9g"
YZ_AGENT_BRANCH_ID = (
    "agtbrch_5501kycttzkmf9ksz96y5mbzpj3f"
)

YZ_AGENT_TOOL_IDS = (
    "tool_1101kyqgpy9be09tep5h83km1rys",
    "tool_1801kyqkvk4zf4jrf1vc8nj3w9re",
    "tool_4401kyqn5v40fq8s0qq3wk0e6emd",
    "tool_0101kyqp6wfdep0ae1fw4ac5caqr",
    "tool_9401kyqr0j43e2at82wya08x2g6p",
)

YZ_EXPECTED_CURRENT_TOOL_IDS: tuple[str, ...] = ()


class YZAgentToolConnectorError(RuntimeError):
    """Raised when the controlled YZ tool attachment is blocked."""


def get_yz_agent_tool_attachment_plan() -> dict:
    """
    Return the fixed YZ attachment plan without changing ElevenLabs.
    """

    return {
        "read_only": True,
        "agent_id": YZ_AGENT_ID,
        "branch_id": YZ_AGENT_BRANCH_ID,
        "expected_current_tool_ids": list(
            YZ_EXPECTED_CURRENT_TOOL_IDS
        ),
        "desired_tool_ids": list(YZ_AGENT_TOOL_IDS),
        "desired_tool_count": len(YZ_AGENT_TOOL_IDS),
        "publishes_agent": False,
        "changes_prompt_text": False,
        "changes_first_message": False,
        "changes_voice": False,
        "changes_asr": False,
        "changes_knowledge_base": False,
        "changes_phone_number": False,
        "changes_supabase": False,
        "advances_provisioning_job": False,
    }


def connect_yz_agent_tools() -> dict:
    """
    Attach exactly the five approved YZ workspace tools to exactly
    YZ Thai Wok & Sushi's verified draft branch.

    Deployment or import of this module does not execute this
    function.

    The underlying client:
    - reads the exact agent branch before writing,
    - requires the current external tool list to be empty,
    - sends only conversation_config.agent.prompt.tool_ids,
    - verifies the exact result after writing,
    - blocks unexpected changes to inspected agent state.

    This function does not publish the agent, change prompt text,
    change the first message, modify voice or ASR settings, replace
    the knowledge base, connect a phone number, update Supabase, or
    advance a provisioning job.
    """

    try:
        result = attach_agent_prompt_tool_ids(
            agent_id=YZ_AGENT_ID,
            branch_id=YZ_AGENT_BRANCH_ID,
            tool_ids=list(YZ_AGENT_TOOL_IDS),
            expected_current_tool_ids=list(
                YZ_EXPECTED_CURRENT_TOOL_IDS
            ),
        )

    except ElevenLabsClientError as error:
        raise YZAgentToolConnectorError(
            "YZ agent tool attachment was safely blocked: "
            f"{error}"
        ) from error

    if not isinstance(result, dict):
        raise YZAgentToolConnectorError(
            "The YZ agent tool attachment returned an invalid result."
        )

    if result.get("success") is not True:
        raise YZAgentToolConnectorError(
            "The YZ agent tool attachment did not report success."
        )

    if result.get("agent_id") != YZ_AGENT_ID:
        raise YZAgentToolConnectorError(
            "The YZ agent tool attachment returned a different agent."
        )

    if result.get("branch_id") != YZ_AGENT_BRANCH_ID:
        raise YZAgentToolConnectorError(
            "The YZ agent tool attachment returned a different branch."
        )

    returned_tool_ids = result.get("tool_ids")

    if returned_tool_ids != list(YZ_AGENT_TOOL_IDS):
        raise YZAgentToolConnectorError(
            "The YZ agent tool attachment returned a different "
            "tool list."
        )

    attached_new_tools = result.get("attached_new_tools")
    reused_existing_attachment = result.get(
        "reused_existing_attachment"
    )

    if (
        attached_new_tools is True
        and reused_existing_attachment is False
    ):
        expected_changed_fields = [
            "conversation_config.agent.prompt.tool_ids",
        ]
    elif (
        attached_new_tools is False
        and reused_existing_attachment is True
    ):
        expected_changed_fields = []
    else:
        raise YZAgentToolConnectorError(
            "The YZ agent tool attachment returned an invalid "
            "attachment status."
        )

    if result.get("changed_fields") != expected_changed_fields:
        raise YZAgentToolConnectorError(
            "The YZ agent tool attachment returned unexpected "
            "changed fields."
        )

    return {
        "success": True,
        "agent_id": YZ_AGENT_ID,
        "branch_id": YZ_AGENT_BRANCH_ID,
        "version_id": result.get("version_id"),
        "tool_ids": list(YZ_AGENT_TOOL_IDS),
        "tool_count": len(YZ_AGENT_TOOL_IDS),
        "attached_new_tools": attached_new_tools,
        "reused_existing_attachment": (
            reused_existing_attachment
        ),
        "changed_fields": expected_changed_fields,
        "published_agent": False,
        "changed_prompt_text": False,
        "changed_first_message": False,
        "changed_voice": False,
        "changed_asr": False,
        "changed_knowledge_base": False,
        "changed_phone_number": False,
        "changed_supabase": False,
        "advanced_provisioning_job": False,
    }
