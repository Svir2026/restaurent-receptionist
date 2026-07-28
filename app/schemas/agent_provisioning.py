from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DuplicateAgentRequest(BaseModel):
    """
    Request for the controlled test-agent duplication endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    restaurant_id: UUID
    provisioning_job_id: UUID
    confirmation: Literal["CREATE_TEST_AGENT"]


class DuplicateAgentResponse(BaseModel):
    """
    Safe response returned after agent provisioning.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    restaurant_id: UUID
    provisioning_job_id: UUID

    agent_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)

    created_new_agent: bool
    recovered_existing_agent: bool

    phone_number_count: int = Field(ge=0)
    idempotent_replay: bool

    next_step: Literal["update_agent"]
