from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from app.schemas.libanon_order_engine import (
    LibanonOrderState,
    LibanonOrderTurnResponse,
)
from app.services.supabase_client import get_client


class VoiceOrderStateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SavedVoiceOrderTransition:
    state: LibanonOrderState
    response: LibanonOrderTurnResponse
    idempotent_replay: bool


class VoiceOrderStateRepository(Protocol):
    def load(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
    ) -> LibanonOrderState | None: ...

    def find_event(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
        event_id: str,
    ) -> LibanonOrderTurnResponse | None: ...

    def save_transition(
        self,
        *,
        expected_revision: int,
        state: LibanonOrderState,
        event_id: str,
        utterance: str,
        response: LibanonOrderTurnResponse,
    ) -> SavedVoiceOrderTransition: ...


class InMemoryVoiceOrderStateRepository:
    """Deterministic test repository with optimistic concurrency."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], LibanonOrderState] = {}
        self._events: dict[tuple[str, str, str], LibanonOrderTurnResponse] = {}
        self._lock = threading.RLock()

    def load(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
    ) -> LibanonOrderState | None:
        with self._lock:
            state = self._states.get((restaurant_id, conversation_id))
            return state.model_copy(deep=True) if state is not None else None

    def find_event(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
        event_id: str,
    ) -> LibanonOrderTurnResponse | None:
        with self._lock:
            response = self._events.get((restaurant_id, conversation_id, event_id))
            return response.model_copy(deep=True) if response is not None else None

    def save_transition(
        self,
        *,
        expected_revision: int,
        state: LibanonOrderState,
        event_id: str,
        utterance: str,
        response: LibanonOrderTurnResponse,
    ) -> SavedVoiceOrderTransition:
        with self._lock:
            key = (state.restaurant_id, state.conversation_id)
            event_key = (*key, event_id)

            existing_event = self._events.get(event_key)
            if existing_event is not None:
                existing_state = self._states[key]
                return SavedVoiceOrderTransition(
                    state=existing_state.model_copy(deep=True),
                    response=existing_event.model_copy(deep=True),
                    idempotent_replay=True,
                )

            current = self._states.get(key)
            current_revision = current.revision if current is not None else 0
            if current_revision != expected_revision:
                raise VoiceOrderStateError(
                    "VOICE_ORDER_REVISION_CONFLICT",
                    "Ordern ändrades samtidigt och måste läsas om.",
                )

            if state.revision != expected_revision + 1:
                raise VoiceOrderStateError(
                    "INVALID_VOICE_ORDER_REVISION",
                    "Orderns versionsnummer är ogiltigt.",
                )

            stored_state = state.model_copy(deep=True)
            stored_response = response.model_copy(
                update={"state_revision": state.revision},
                deep=True,
            )
            self._states[key] = stored_state
            self._events[event_key] = stored_response

            return SavedVoiceOrderTransition(
                state=stored_state.model_copy(deep=True),
                response=stored_response.model_copy(deep=True),
                idempotent_replay=False,
            )


def build_voice_order_event_id(
    *,
    conversation_id: str,
    entry_index: int,
    utterance: str,
) -> str:
    canonical = json.dumps(
        {
            "conversation_id": conversation_id,
            "entry_index": entry_index,
            "utterance": utterance,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SupabaseVoiceOrderStateRepository:
    """
    Persistent repository for production-like test environments.

    Writes require the SQL migration shipped with this branch. The save RPC
    performs event idempotency and revision compare-and-swap atomically.
    """

    @staticmethod
    def _first_row(value: object) -> dict[str, Any] | None:
        if isinstance(value, list):
            value = value[0] if value else None
        return value if isinstance(value, dict) else None

    def load(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
    ) -> LibanonOrderState | None:
        try:
            result = (
                get_client()
                .table("voice_order_sessions")
                .select("state")
                .eq("restaurant_id", restaurant_id)
                .eq("conversation_id", conversation_id)
                .limit(1)
                .execute()
            )
        except Exception as error:
            raise VoiceOrderStateError(
                "VOICE_ORDER_STATE_UNAVAILABLE",
                "Orderläget kunde inte läsas.",
            ) from error

        row = self._first_row(result.data)
        if row is None:
            return None

        try:
            return LibanonOrderState.model_validate(row["state"])
        except Exception as error:
            raise VoiceOrderStateError(
                "INVALID_VOICE_ORDER_STATE",
                "Det sparade orderläget är ogiltigt.",
            ) from error

    def find_event(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
        event_id: str,
    ) -> LibanonOrderTurnResponse | None:
        try:
            result = (
                get_client()
                .table("voice_order_events")
                .select("response")
                .eq("restaurant_id", restaurant_id)
                .eq("conversation_id", conversation_id)
                .eq("event_id", event_id)
                .limit(1)
                .execute()
            )
        except Exception as error:
            raise VoiceOrderStateError(
                "VOICE_ORDER_STATE_UNAVAILABLE",
                "Orderhändelsen kunde inte läsas.",
            ) from error

        row = self._first_row(result.data)
        if row is None:
            return None
        return LibanonOrderTurnResponse.model_validate(row["response"])

    def save_transition(
        self,
        *,
        expected_revision: int,
        state: LibanonOrderState,
        event_id: str,
        utterance: str,
        response: LibanonOrderTurnResponse,
    ) -> SavedVoiceOrderTransition:
        request_hash = hashlib.sha256(utterance.encode("utf-8")).hexdigest()

        try:
            result = (
                get_client()
                .rpc(
                    "save_voice_order_transition",
                    {
                        "p_restaurant_id": state.restaurant_id,
                        "p_conversation_id": state.conversation_id,
                        "p_expected_revision": expected_revision,
                        "p_state": state.model_dump(mode="json"),
                        "p_event_id": event_id,
                        "p_request_hash": request_hash,
                        "p_response": response.model_dump(mode="json"),
                    },
                )
                .execute()
            )
        except Exception as error:
            raw_message = str(getattr(error, "message", None) or error)
            code = (
                "VOICE_ORDER_REVISION_CONFLICT"
                if "VOICE_ORDER_REVISION_CONFLICT" in raw_message
                else "VOICE_ORDER_STATE_WRITE_FAILED"
            )
            raise VoiceOrderStateError(
                code,
                "Orderläget kunde inte sparas säkert.",
            ) from error

        row = self._first_row(result.data)
        if row is None:
            raise VoiceOrderStateError(
                "EMPTY_VOICE_ORDER_STATE_RESPONSE",
                "Orderlagringen gav inget svar.",
            )

        try:
            stored_state = LibanonOrderState.model_validate(row["result_state"])
            stored_response = LibanonOrderTurnResponse.model_validate(
                row["result_response"]
            )
            replay = bool(row["idempotent_replay"])
        except Exception as error:
            raise VoiceOrderStateError(
                "INVALID_VOICE_ORDER_STATE_RESPONSE",
                "Orderlagringen gav ett ogiltigt svar.",
            ) from error

        return SavedVoiceOrderTransition(
            state=stored_state,
            response=stored_response,
            idempotent_replay=replay,
        )
