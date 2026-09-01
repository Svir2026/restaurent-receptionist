from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LibanonOrderStatus = Literal[
    "collecting",
    "awaiting_confirmation",
    "ready_to_submit",
    "stopped",
]

LibanonOrderAction = Literal[
    "ask_question",
    "confirm_delta",
    "confirm_full_order",
    "repeat_unknown_item",
    "reject_unknown_item",
    "technical_stop",
    "no_change",
]

LibanonQuestionKind = Literal[
    "catalog_ambiguity",
    "fuzzy_confirmation",
    "required_option",
]


class LibanonEngineModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class LibanonOrderTurnRequest(LibanonEngineModel):
    conversation_id: str = Field(min_length=8, max_length=200)
    conversation_history: list[dict[str, Any]] | dict[str, Any] | str
    customer_phone: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def parse_conversation_history(self) -> "LibanonOrderTurnRequest":
        history = self.conversation_history

        if isinstance(history, str):
            try:
                history = json.loads(history)
            except json.JSONDecodeError as error:
                raise ValueError(
                    "conversation_history must contain valid JSON"
                ) from error

        if isinstance(history, dict):
            history = history.get("entries")

        if not isinstance(history, list) or not history:
            raise ValueError("conversation_history must contain at least one entry")

        if len(history) > 200:
            raise ValueError("conversation_history contains too many entries")

        if not all(isinstance(value, dict) for value in history):
            raise ValueError("conversation_history entries must be objects")

        if len(json.dumps(history, ensure_ascii=False)) > 100_000:
            raise ValueError("conversation_history is too large")

        self.conversation_history = history
        return self


class LibanonSelectedOption(LibanonEngineModel):
    group_source_key: str = Field(min_length=1, max_length=120)
    option_source_key: str = Field(min_length=1, max_length=120)
    group_name: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    kitchen_name: str = Field(min_length=1, max_length=200)
    price_delta_minor: int


class LibanonOrderNote(LibanonEngineModel):
    kind: Literal["remove", "extra", "instruction"]
    text: str = Field(min_length=1, max_length=300)


class LibanonOrderLine(LibanonEngineModel):
    line_id: str = Field(min_length=8, max_length=80)
    item_source_key: str = Field(min_length=1, max_length=120)
    official_name: str = Field(min_length=1, max_length=200)
    customer_display_name: str = Field(min_length=1, max_length=200)
    kitchen_display_name: str = Field(min_length=1, max_length=200)
    category_name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=100)
    base_price_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    selected_options: list[LibanonSelectedOption] = Field(default_factory=list)
    notes: list[LibanonOrderNote] = Field(default_factory=list)
    price_verification_status: str = Field(min_length=1, max_length=50)
    pricing_complete: bool = True


class LibanonPendingQuestion(LibanonEngineModel):
    question_id: str = Field(min_length=8, max_length=80)
    kind: LibanonQuestionKind
    prompt: str = Field(min_length=1, max_length=500)
    line_id: str | None = Field(default=None, max_length=80)
    group_source_key: str | None = Field(default=None, max_length=120)
    candidate_item_source_keys: list[str] = Field(default_factory=list)
    candidate_option_source_keys: list[str] = Field(default_factory=list)
    fuzzy_item_source_key: str | None = Field(default=None, max_length=120)
    original_utterance: str | None = Field(default=None, max_length=1000)


class LibanonOrderState(LibanonEngineModel):
    restaurant_id: str = Field(min_length=36, max_length=36)
    conversation_id: str = Field(min_length=8, max_length=200)
    revision: int = Field(default=0, ge=0)
    status: LibanonOrderStatus = "collecting"
    items: list[LibanonOrderLine] = Field(default_factory=list)
    pending_questions: list[LibanonPendingQuestion] = Field(default_factory=list)
    unresolved_attempts: int = Field(default=0, ge=0, le=3)
    processed_event_ids: list[str] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LibanonOrderTurnResponse(LibanonEngineModel):
    success: bool
    action: LibanonOrderAction
    say: str = Field(min_length=1, max_length=1500)
    event_id: str = Field(min_length=16, max_length=64)
    idempotent_replay: bool
    state_revision: int = Field(ge=0)
    cart_changed: bool
    order_ready: bool
    submission_allowed: bool
    submission_blocked_reason: str | None = Field(default=None, max_length=500)
    delta_lines: list[LibanonOrderLine] = Field(default_factory=list)
    cart: list[LibanonOrderLine] = Field(default_factory=list)
    pending_questions: list[LibanonPendingQuestion] = Field(default_factory=list)
