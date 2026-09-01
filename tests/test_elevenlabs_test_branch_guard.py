from __future__ import annotations

import os
import unittest
from copy import deepcopy
from unittest.mock import patch


os.environ.setdefault("RESTAURANT_TIMEZONE", "Europe/Stockholm")
os.environ.setdefault("ELEVENLABS_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault(
    "SVIR_INTERNAL_API_SECRET",
    "test-internal-secret-that-is-long-enough",
)
os.environ.setdefault("ELEVENLABS_API_KEY", "test-api-key")
os.environ.setdefault("ELEVENLABS_TEMPLATE_AGENT_ID", "test-agent")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from app.services.elevenlabs_client import (
    ElevenLabsClientError,
    replace_test_branch_prompt_tool_ids,
    update_test_branch_prompt_text,
)


AGENT_ID = "agent_libanon"
MAIN_BRANCH_ID = "agtbrch_main"
TEST_BRANCH_ID = "agtbrch_test"


def _agent_payload(branch_id: str) -> dict:
    return {
        "agent_id": AGENT_ID,
        "branch_id": branch_id,
        "main_branch_id": MAIN_BRANCH_ID,
        "version_id": "agtvrsn_main" if branch_id == MAIN_BRANCH_ID else "agtvrsn_test",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": "Prompt",
                    "tool_ids": ["tool_old"],
                    "tools": [{"name": "old"}],
                    "knowledge_base": [{"id": "kb_libanon"}],
                }
            }
        },
        "platform_settings": {},
        "workflow": {},
        "phone_numbers": [],
        "whatsapp_accounts": [],
        "tags": [],
    }


class ElevenLabsTestBranchGuardTests(unittest.TestCase):
    def test_tool_replacement_refuses_main_before_any_request(self) -> None:
        with patch(
            "app.services.elevenlabs_client._read_agent_branch_payload"
        ) as read_branch:
            with self.assertRaises(ElevenLabsClientError):
                replace_test_branch_prompt_tool_ids(
                    agent_id=AGENT_ID,
                    branch_id=MAIN_BRANCH_ID,
                    main_branch_id=MAIN_BRANCH_ID,
                    tool_ids=["tool_new"],
                    expected_current_tool_ids=["tool_old"],
                )

        read_branch.assert_not_called()

    def test_prompt_update_refuses_main_before_any_request(self) -> None:
        with patch(
            "app.services.elevenlabs_client._read_agent_branch_payload"
        ) as read_branch:
            with self.assertRaises(ElevenLabsClientError):
                update_test_branch_prompt_text(
                    agent_id=AGENT_ID,
                    branch_id=MAIN_BRANCH_ID,
                    main_branch_id=MAIN_BRANCH_ID,
                    prompt_text="New prompt",
                    expected_current_prompt_sha256="a" * 64,
                    required_tool_ids=["tool_new"],
                    required_knowledge_base_document_ids=["kb_libanon"],
                )

        read_branch.assert_not_called()

    def test_tool_replacement_accepts_one_tool_and_guards_main(self) -> None:
        main = _agent_payload(MAIN_BRANCH_ID)
        test = _agent_payload(TEST_BRANCH_ID)
        with patch(
            "app.services.elevenlabs_client._read_agent_branch_payload",
            side_effect=[main, test, deepcopy(main)],
        ), patch(
            "app.services.elevenlabs_client.attach_agent_prompt_tool_ids",
            return_value={"success": True},
        ) as attach:
            result = replace_test_branch_prompt_tool_ids(
                agent_id=AGENT_ID,
                branch_id=TEST_BRANCH_ID,
                main_branch_id=MAIN_BRANCH_ID,
                tool_ids=["tool_new"],
                expected_current_tool_ids=["tool_old"],
            )

        self.assertTrue(result["success"])
        self.assertFalse(attach.call_args.kwargs["require_exactly_five"])

    def test_tool_replacement_detects_concurrent_main_change(self) -> None:
        main = _agent_payload(MAIN_BRANCH_ID)
        changed_main = deepcopy(main)
        changed_main["version_id"] = "agtvrsn_changed"
        with patch(
            "app.services.elevenlabs_client._read_agent_branch_payload",
            side_effect=[main, _agent_payload(TEST_BRANCH_ID), changed_main],
        ), patch(
            "app.services.elevenlabs_client.attach_agent_prompt_tool_ids",
            return_value={"success": True},
        ):
            with self.assertRaises(ElevenLabsClientError):
                replace_test_branch_prompt_tool_ids(
                    agent_id=AGENT_ID,
                    branch_id=TEST_BRANCH_ID,
                    main_branch_id=MAIN_BRANCH_ID,
                    tool_ids=["tool_new"],
                    expected_current_tool_ids=["tool_old"],
                )


if __name__ == "__main__":
    unittest.main()
