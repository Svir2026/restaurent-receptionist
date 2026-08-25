from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.core.tool_auth import ToolRestaurantContext
from app.schemas.restaurant_tools_v2 import (
    ResolveMenuItemsV2Request,
)
from app.services.restaurant_menu_pricing import (
    MENU_ITEM_NAME_FIELDS,
    RestaurantMenuPricingError,
    _load_active_menu_items,
    _parse_menu_item_id,
)
from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)

YZ_MENU_RESOLVER_TOOL_NAME = (
    "svir_yz_thai_wok_sushi_resolve_menu_items_v2"
)

RECOVERY_MESSAGES = {
    1: "Ursäkta, kan du upprepa vilken rätt du ville ha?",
    2: (
        "Tyvärr, det finns inte på menyn. "
        "Testa gärna att beställa något annat."
    ),
    3: (
        "Det verkar vara ett tekniskt fel just nu. "
        "Kom gärna in i restaurangen och beställ."
    ),
}


class RestaurantMenuResolverError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class _ResolverPhrase:
    normalized_text: str
    words: tuple[str, ...]
    item: dict[str, Any]
    source: Literal["canonical", "alias"]


def _normalize_spoken_text(value: object) -> str:
    """Normalize case, whitespace, and punctuation without fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    characters = [
        character
        if not unicodedata.category(character).startswith(("P", "S"))
        else " "
        for character in normalized.casefold()
    ]
    return " ".join("".join(characters).split())


def _load_menu_item_aliases(
    restaurant_id: UUID,
    active_item_ids: set[str],
) -> list[dict[str, Any]]:
    try:
        response = (
            get_client()
            .table("menu_item_aliases")
            .select(
                "id,restaurant_id,menu_item_id,alias,"
                "normalized_alias,alias_type,priority"
            )
            .eq("restaurant_id", str(restaurant_id))
            .execute()
        )
    except Exception as error:
        logger.error(
            "Could not read restaurant menu aliases",
            extra={
                "restaurant_id": str(restaurant_id),
                "error_type": type(error).__name__,
            },
        )
        raise RestaurantMenuResolverError(
            code="RESTAURANT_ALIAS_READ_FAILED",
            message="Restaurangens godkända alias kunde inte läsas.",
            status_code=502,
        ) from error

    if response.data is None:
        return []
    if not isinstance(response.data, list):
        raise RestaurantMenuResolverError(
            code="INVALID_RESTAURANT_ALIAS_RESPONSE",
            message="Restaurangens alias gav ett ogiltigt svar.",
            status_code=502,
        )

    aliases: list[dict[str, Any]] = []
    for value in response.data:
        if not isinstance(value, dict):
            raise RestaurantMenuResolverError(
                code="INVALID_RESTAURANT_ALIAS_RESPONSE",
                message="Restaurangens alias innehåller ogiltiga uppgifter.",
                status_code=502,
            )
        if str(value.get("restaurant_id") or "") != str(restaurant_id):
            raise RestaurantMenuResolverError(
                code="ALIAS_RESTAURANT_MISMATCH",
                message="Aliaslistan kunde inte verifieras mot restaurangen.",
                status_code=502,
            )
        if str(value.get("menu_item_id") or "") not in active_item_ids:
            continue
        aliases.append(value)

    return aliases


def _history_entries(request: ResolveMenuItemsV2Request) -> list[dict[str, Any]]:
    return [
        entry
        for entry in request.conversation_history
        if isinstance(entry, dict)
    ]


def _latest_user_utterance(entries: list[dict[str, Any]]) -> str:
    for entry in reversed(entries):
        role = str(entry.get("role") or "").casefold()
        if role not in {"user", "customer"}:
            continue

        for field_name in ("message", "content", "text"):
            value = entry.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()

    raise RestaurantMenuResolverError(
        code="USER_UTTERANCE_MISSING",
        message="Det senaste kundyttrandet kunde inte läsas.",
        status_code=422,
    )


def _tool_result_status(entry: dict[str, Any]) -> str | None:
    tool_name = str(
        entry.get("tool_name")
        or entry.get("name")
        or ""
    ).strip()
    if tool_name != YZ_MENU_RESOLVER_TOOL_NAME:
        return None

    value: object = (
        entry.get("result_value")
        or entry.get("result")
        or entry.get("output")
    )
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None

    if not isinstance(value, dict):
        return None

    status = str(value.get("status") or "").upper()
    return status if status in {"MATCH", "NO_MATCH"} else None


def _previous_unresolved_attempts(entries: list[dict[str, Any]]) -> int:
    attempts = 0
    for entry in entries:
        status = _tool_result_status(entry)
        if status == "MATCH":
            attempts = 0
        elif status == "NO_MATCH":
            attempts = min(attempts + 1, 3)
    return attempts


def _build_phrases(
    menu_items: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
) -> list[_ResolverPhrase]:
    items_by_id = {
        str(_parse_menu_item_id(item.get("id"))): item
        for item in menu_items
    }
    phrases: dict[tuple[str, str], _ResolverPhrase] = {}

    for item_id, item in items_by_id.items():
        for field_name in MENU_ITEM_NAME_FIELDS:
            normalized = _normalize_spoken_text(item.get(field_name))
            if normalized:
                phrases[(normalized, item_id)] = _ResolverPhrase(
                    normalized_text=normalized,
                    words=tuple(normalized.split()),
                    item=item,
                    source="canonical",
                )

    for alias in aliases:
        item_id = str(alias.get("menu_item_id") or "")
        item = items_by_id.get(item_id)
        normalized = _normalize_spoken_text(alias.get("alias"))
        if item is None or not normalized:
            continue

        key = (normalized, item_id)
        if key not in phrases:
            phrases[key] = _ResolverPhrase(
                normalized_text=normalized,
                words=tuple(normalized.split()),
                item=item,
                source="alias",
            )

    return sorted(
        phrases.values(),
        key=lambda phrase: (
            -len(phrase.words),
            phrase.normalized_text,
            str(phrase.item.get("id")),
        ),
    )


def _find_matches(
    utterance: str,
    phrases: list[_ResolverPhrase],
) -> tuple[list[_ResolverPhrase], bool]:
    words = tuple(_normalize_spoken_text(utterance).split())
    candidates: list[tuple[int, int, _ResolverPhrase]] = []

    for phrase in phrases:
        width = len(phrase.words)
        if width == 0 or width > len(words):
            continue
        for start in range(0, len(words) - width + 1):
            if words[start : start + width] == phrase.words:
                candidates.append((start, start + width, phrase))

    if not candidates:
        return [], False

    phrase_targets: dict[tuple[int, int, str], set[str]] = {}
    for start, end, phrase in candidates:
        key = (start, end, phrase.normalized_text)
        phrase_targets.setdefault(key, set()).add(
            str(phrase.item.get("id"))
        )
    if any(len(item_ids) > 1 for item_ids in phrase_targets.values()):
        return [], True

    selected: list[tuple[int, int, _ResolverPhrase]] = []
    occupied: set[int] = set()
    for start, end, phrase in sorted(
        candidates,
        key=lambda value: (
            -(value[1] - value[0]),
            value[0],
            0 if value[2].source == "canonical" else 1,
        ),
    ):
        span = set(range(start, end))
        if occupied.intersection(span):
            continue
        selected.append((start, end, phrase))
        occupied.update(span)

    selected.sort(key=lambda value: value[0])
    unique: list[_ResolverPhrase] = []
    seen_item_ids: set[str] = set()
    for _, _, phrase in selected:
        item_id = str(phrase.item.get("id"))
        if item_id not in seen_item_ids:
            unique.append(phrase)
            seen_item_ids.add(item_id)
    return unique, False


def resolve_restaurant_menu_items(
    *,
    context: ToolRestaurantContext,
    request: ResolveMenuItemsV2Request,
) -> dict[str, Any]:
    entries = _history_entries(request)
    utterance = _latest_user_utterance(entries)
    try:
        menu_items = _load_active_menu_items(context.restaurant_id)
    except RestaurantMenuPricingError as error:
        raise RestaurantMenuResolverError(
            code=error.code,
            message=error.message,
            status_code=error.status_code,
        ) from error

    if not menu_items:
        raise RestaurantMenuResolverError(
            code="RESTAURANT_MENU_EMPTY",
            message="Restaurangen har inga aktiva produkter i menyn.",
            status_code=422,
        )

    active_item_ids = {
        str(_parse_menu_item_id(item.get("id")))
        for item in menu_items
    }
    aliases = _load_menu_item_aliases(
        context.restaurant_id,
        active_item_ids,
    )
    phrases = _build_phrases(menu_items, aliases)
    matches, ambiguous = _find_matches(utterance, phrases)

    if ambiguous:
        return {
            "success": True,
            "status": "AMBIGUOUS",
            "action": "clarify",
            "unresolved_attempt": 0,
            "stop_recovery": False,
            "customer_message": None,
            "matches": [],
        }

    if matches:
        return {
            "success": True,
            "status": "MATCH",
            "action": "continue",
            "unresolved_attempt": 0,
            "stop_recovery": False,
            "customer_message": None,
            "matches": [
                {
                    "menu_item_id": _parse_menu_item_id(
                        phrase.item.get("id")
                    ),
                    "official_name": str(
                        phrase.item.get("official_name") or ""
                    ).strip(),
                    "customer_display_name": str(
                        phrase.item.get("customer_display_name")
                        or phrase.item.get("official_name")
                        or ""
                    ).strip(),
                    "matched_text": phrase.normalized_text,
                    "match_source": phrase.source,
                }
                for phrase in matches
            ],
        }

    unresolved_attempt = min(
        _previous_unresolved_attempts(entries) + 1,
        3,
    )
    action = {
        1: "repeat",
        2: "not_on_menu",
        3: "technical_stop",
    }[unresolved_attempt]
    return {
        "success": True,
        "status": "NO_MATCH",
        "action": action,
        "unresolved_attempt": unresolved_attempt,
        "stop_recovery": unresolved_attempt == 3,
        "customer_message": RECOVERY_MESSAGES[
            unresolved_attempt
        ],
        "matches": [],
    }
