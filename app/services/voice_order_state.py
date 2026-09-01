from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
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


class SQLiteVoiceOrderStateRepository:
    """Persistent, isolated repository for a single-replica test deployment."""

    def __init__(self, database_path: str) -> None:
        path = Path(database_path).expanduser()
        if not path.is_absolute():
            raise VoiceOrderStateError(
                "INVALID_SQLITE_STATE_PATH",
                "SQLite-sökvägen måste vara absolut.",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self._database_path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                pragma journal_mode = wal;
                create table if not exists voice_order_sessions (
                    restaurant_id text not null,
                    conversation_id text not null,
                    revision integer not null check (revision >= 1),
                    state_json text not null,
                    expires_at real not null,
                    primary key (restaurant_id, conversation_id)
                );
                create table if not exists voice_order_events (
                    restaurant_id text not null,
                    conversation_id text not null,
                    event_id text not null,
                    request_hash text not null,
                    response_json text not null,
                    primary key (restaurant_id, conversation_id, event_id),
                    foreign key (restaurant_id, conversation_id)
                        references voice_order_sessions(
                            restaurant_id,
                            conversation_id
                        ) on delete cascade
                );
                create index if not exists voice_order_sessions_expires_at_idx
                    on voice_order_sessions (expires_at);
                """
            )

    @staticmethod
    def _parse_state(value: str) -> LibanonOrderState:
        return LibanonOrderState.model_validate_json(value)

    @staticmethod
    def _parse_response(value: str) -> LibanonOrderTurnResponse:
        return LibanonOrderTurnResponse.model_validate_json(value)

    def load(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
    ) -> LibanonOrderState | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    select state_json
                      from voice_order_sessions
                     where restaurant_id = ?
                       and conversation_id = ?
                       and expires_at >= ?
                    """,
                    (restaurant_id, conversation_id, time.time()),
                ).fetchone()
        except (sqlite3.Error, ValueError) as error:
            raise VoiceOrderStateError(
                "VOICE_ORDER_STATE_UNAVAILABLE",
                "Orderläget kunde inte läsas.",
            ) from error
        return self._parse_state(row["state_json"]) if row is not None else None

    def find_event(
        self,
        *,
        restaurant_id: str,
        conversation_id: str,
        event_id: str,
    ) -> LibanonOrderTurnResponse | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    select event.response_json
                      from voice_order_events as event
                      join voice_order_sessions as session
                        on session.restaurant_id = event.restaurant_id
                       and session.conversation_id = event.conversation_id
                     where event.restaurant_id = ?
                       and event.conversation_id = ?
                       and event.event_id = ?
                       and session.expires_at >= ?
                    """,
                    (restaurant_id, conversation_id, event_id, time.time()),
                ).fetchone()
        except sqlite3.Error as error:
            raise VoiceOrderStateError(
                "VOICE_ORDER_STATE_UNAVAILABLE",
                "Orderhändelsen kunde inte läsas.",
            ) from error
        return self._parse_response(row["response_json"]) if row is not None else None

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
        if state.revision != expected_revision + 1:
            raise VoiceOrderStateError(
                "INVALID_VOICE_ORDER_REVISION",
                "Orderns versionsnummer är ogiltigt.",
            )
        if response.event_id != event_id or response.state_revision != state.revision:
            raise VoiceOrderStateError(
                "VOICE_ORDER_RESPONSE_IDENTITY_MISMATCH",
                "Ordersvaret tillhör inte övergången.",
            )

        connection = self._connect()
        try:
            connection.execute("begin immediate")
            now = time.time()
            connection.execute(
                """
                delete from voice_order_sessions
                 where restaurant_id = ?
                   and conversation_id = ?
                   and expires_at < ?
                """,
                (state.restaurant_id, state.conversation_id, now),
            )
            existing_event = connection.execute(
                """
                select request_hash, response_json
                  from voice_order_events
                 where restaurant_id = ?
                   and conversation_id = ?
                   and event_id = ?
                """,
                (state.restaurant_id, state.conversation_id, event_id),
            ).fetchone()
            if existing_event is not None:
                if existing_event["request_hash"] != request_hash:
                    raise VoiceOrderStateError(
                        "VOICE_ORDER_EVENT_PAYLOAD_MISMATCH",
                        "Samma händelse-ID användes för olika innehåll.",
                    )
                stored_row = connection.execute(
                    """
                    select state_json
                      from voice_order_sessions
                     where restaurant_id = ? and conversation_id = ?
                    """,
                    (state.restaurant_id, state.conversation_id),
                ).fetchone()
                assert stored_row is not None
                connection.commit()
                return SavedVoiceOrderTransition(
                    state=self._parse_state(stored_row["state_json"]),
                    response=self._parse_response(existing_event["response_json"]),
                    idempotent_replay=True,
                )

            current = connection.execute(
                """
                select revision
                  from voice_order_sessions
                 where restaurant_id = ? and conversation_id = ?
                """,
                (state.restaurant_id, state.conversation_id),
            ).fetchone()
            current_revision = int(current["revision"]) if current is not None else 0
            if current_revision != expected_revision:
                raise VoiceOrderStateError(
                    "VOICE_ORDER_REVISION_CONFLICT",
                    "Ordern ändrades samtidigt och måste läsas om.",
                )

            state_json = state.model_dump_json()
            response_json = response.model_dump_json()
            connection.execute(
                """
                insert into voice_order_sessions (
                    restaurant_id,
                    conversation_id,
                    revision,
                    state_json,
                    expires_at
                ) values (?, ?, ?, ?, ?)
                on conflict (restaurant_id, conversation_id) do update set
                    revision = excluded.revision,
                    state_json = excluded.state_json,
                    expires_at = excluded.expires_at
                """,
                (
                    state.restaurant_id,
                    state.conversation_id,
                    state.revision,
                    state_json,
                    now + 86_400,
                ),
            )
            connection.execute(
                """
                insert into voice_order_events (
                    restaurant_id,
                    conversation_id,
                    event_id,
                    request_hash,
                    response_json
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    state.restaurant_id,
                    state.conversation_id,
                    event_id,
                    request_hash,
                    response_json,
                ),
            )
            connection.commit()
        except VoiceOrderStateError:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise VoiceOrderStateError(
                "VOICE_ORDER_STATE_WRITE_FAILED",
                "Orderläget kunde inte sparas säkert.",
            ) from error
        finally:
            connection.close()

        return SavedVoiceOrderTransition(
            state=state.model_copy(deep=True),
            response=response.model_copy(deep=True),
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
