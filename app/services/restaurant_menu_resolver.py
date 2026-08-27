from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from threading import Lock
from time import monotonic
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
from app.services.elevenlabs_tool_definitions import (
    YZ_MENU_RESOLVER_V2_TOOL_NAME,
    YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME,
)
from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)

YZ_MENU_RESOLVER_TOOL_NAME = YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME
YZ_MENU_RESOLVER_TOOL_NAMES = frozenset(
    {
        YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME,
        YZ_MENU_RESOLVER_V2_TOOL_NAME,
    }
)

APPROVED_ALIAS_OVERRIDES = {
    "yz-thai-wok-sushi": {
        "yakinaki": "24. Yakiniku",
        "yakiniki": "24. Yakiniku",
        "yakniki": "24. Yakiniku",
        "kycklingspett": "23. Satay Gai",
        "kycklingpsett": "23. Satay Gai",
        "kycklingpasett": "23. Satay Gai",
        "kycklingspett med jordnötsås": "23. Satay Gai",
    },
}

APPROVED_VARIANT_FAMILIES = {
    "yz-thai-wok-sushi": {
        "pad thai": (
            "pad thai",
            "Vilket protein vill du ha?",
        ),
        "röd curry": (
            "gaeng ped",
            "Vilket protein vill du ha?",
        ),
        "rad curry": (
            "gaeng ped",
            "Vilket protein vill du ha?",
        ),
        "red curry": (
            "gaeng ped",
            "Vilket protein vill du ha?",
        ),
        "gaeng ped": (
            "gaeng ped",
            "Vilket protein vill du ha?",
        ),
        "grön curry": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "gron curry": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "gran curry": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "grand curry": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "grann curry": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "gren curry": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "green curry": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "gaeng keowan": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "gäng keowan": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "keowan": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "nummer 2": (
            "gaeng keowan",
            "Vilket protein vill du ha?",
        ),
        "gaeng panang": (
            "gaeng panang",
            "Vilket protein vill du ha?",
        ),
        "panang": (
            "gaeng panang",
            "Vilket protein vill du ha?",
        ),
        "panang curry": (
            "gaeng panang",
            "Vilket protein vill du ha?",
        ),
        "penang": (
            "gaeng panang",
            "Vilket protein vill du ha?",
        ),
        "nummer 3": (
            "gaeng panang",
            "Vilket protein vill du ha?",
        ),
        "massamang curry": (
            "massamang curry",
            "Vilket protein vill du ha?",
        ),
        "massaman curry": (
            "massamang curry",
            "Vilket protein vill du ha?",
        ),
        "massamang": (
            "massamang curry",
            "Vilket protein vill du ha?",
        ),
        "massaman": (
            "massamang curry",
            "Vilket protein vill du ha?",
        ),
        "matsaman": (
            "massamang curry",
            "Vilket protein vill du ha?",
        ),
        "nummer 4": (
            "massamang curry",
            "Vilket protein vill du ha?",
        ),
        "pad krapow": (
            "pad krapow",
            "Vilket protein vill du ha?",
        ),
        "krapow": (
            "pad krapow",
            "Vilket protein vill du ha?",
        ),
        "kra pow": (
            "pad krapow",
            "Vilket protein vill du ha?",
        ),
        "pad kaprao": (
            "pad krapow",
            "Vilket protein vill du ha?",
        ),
        "basilika stark": (
            "pad krapow",
            "Vilket protein vill du ha?",
        ),
        "nummer 9": (
            "pad krapow",
            "Vilket protein vill du ha?",
        ),
        "pad priawan": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "pad privan": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "priawan": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "priewan": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "sötsur wok": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "sotsur wok": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "sweet and sour": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "nummer 11": (
            "pad priawan",
            "Vilket protein vill du ha?",
        ),
        "cashewnötter": (
            "pad med mamuang",
            "Vilket protein vill du ha?",
        ),
        "cashew": (
            "pad med mamuang",
            "Vilket protein vill du ha?",
        ),
    },
}

VERIFIED_PROTEIN_VARIANTS = {
    "anka",
    "biff",
    "bläckfisk",
    "fläsk",
    "kyckling",
    "räkor",
    "tofu",
}

APPROVED_PROTEIN_ALIASES = {
    "gris": "fläsk",
    "kycklingpapadah": "kyckling",
}

# Explicit Swedish references customers use after the agent asks which
# protein belongs to several pending curry dishes. They identify a known
# family; they are never used to invent a new menu item.
APPROVED_VARIANT_FAMILY_REFERENCE_WORDS = {
    "gaeng ped": frozenset({"röd", "röda", "rod", "roda", "red"}),
    "gaeng keowan": frozenset(
        {"grön", "gröna", "gron", "grona", "green"}
    ),
    "gaeng panang": frozenset({"panang", "penang", "paneng"}),
    "massamang curry": frozenset(
        {"massaman", "massamang", "matsaman"}
    ),
    "pad krapow": frozenset({"krapow", "kaprao"}),
    "pad priawan": frozenset({"priawan", "priewan", "privan"}),
    "pad med mamuang": frozenset({"cashew", "cashewnötter"}),
}

VARIANT_FOLLOW_UP_FILLER_WORDS = {
    "alltså",
    "eh",
    "hm",
    "ja",
    "okej",
    "äh",
    "öh",
    "öhm",
}

# ASR can insert a short hesitation inside a dish name, for example
# "gran, eh, curry". These are ignored only by the bounded family matcher.
VARIANT_FAMILY_FILLER_WORDS = {
    "eh",
    "ehm",
    "ehh",
    "hm",
    "hmm",
    "öh",
    "öhm",
    "um",
}
VARIANT_FAMILY_FUZZY_MINIMUM_RATIO = 0.90
VARIANT_FAMILY_FUZZY_MINIMUM_MARGIN = 0.08

# General menu fuzz is intentionally stricter than the small family matcher.
# It is only consulted after exact canonical names, approved aliases, and the
# deterministic variant layer all fail. One uniquely strong candidate is
# accepted; ties are left for the recovery flow instead of becoming an order.
MENU_FUZZY_MINIMUM_RATIO = 0.93
MENU_FUZZY_MINIMUM_MARGIN = 0.07
MENU_FUZZY_MINIMUM_SINGLE_WORD_LENGTH = 7
MENU_FUZZY_FILLER_WORDS = VARIANT_FAMILY_FILLER_WORDS | {
    "alltså",
    "beställa",
    "en",
    "ett",
    "gärna",
    "ha",
    "hej",
    "jag",
    "kan",
    "skulle",
    "ta",
    "tack",
    "vill",
    "villha",
}

RESOLVER_CATALOG_CACHE_TTL_SECONDS = 30.0
ALIAS_PAGE_SIZE = 1000
_catalog_cache: dict[
    str,
    tuple[float, list[dict[str, Any]], list[dict[str, Any]]],
] = {}
_catalog_cache_lock = Lock()
_catalog_load_lock = Lock()

VERIFIED_SPOKEN_NUMBER_TOKENS = {
    "fem": "5",
}

YZ_SUSHI_REGULAR_NAMES = {
    8: "Liten Sushi – 8 bitar",
    10: "Mellan Sushi – 10 bitar",
    12: "Stor Sushi – 12 bitar",
    15: "Extra Stor Sushi – 15 bitar",
    20: "Super Sushi – 20 bitar",
    30: "Familje Sushi – 30 bitar",
    50: "Stor Familje Sushi – 50 bitar",
}

YZ_SUSHI_SIZE_TOKENS = {
    "8": 8,
    "åtta": 8,
    "10": 10,
    "tio": 10,
    "12": 12,
    "tolv": 12,
    "15": 15,
    "femton": 15,
    "20": 20,
    "tjugo": 20,
    "30": 30,
    "trettio": 30,
    "50": 50,
    "femtio": 50,
}

YZ_SUSHI_SIZE_WORDS = {
    8: "åtta",
    10: "tio",
    12: "tolv",
    15: "femton",
    20: "tjugo",
    30: "trettio",
    50: "femtio",
}

YZ_SUSHI_COMPOUND_SIZE_TOKENS = {
    f"{word}bitars": size
    for size, word in YZ_SUSHI_SIZE_WORDS.items()
}

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
    source: Literal["canonical", "alias", "fuzzy"]


_phrase_cache: dict[str, tuple[float, list[_ResolverPhrase]]] = {}


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


def _normalize_match_text(value: object) -> str:
    words = _normalize_spoken_text(value).split()
    return " ".join(
        VERIFIED_SPOKEN_NUMBER_TOKENS.get(word, word)
        for word in words
    )


def _load_menu_item_aliases(
    restaurant_id: UUID,
    active_item_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_alias_ids: set[str] = set()
    try:
        offset = 0
        while True:
            response = (
                get_client()
                .table("menu_item_aliases")
                .select(
                    "id,restaurant_id,menu_item_id,alias,"
                    "normalized_alias,alias_type,priority"
                )
                .eq("restaurant_id", str(restaurant_id))
                .order("id")
                .range(offset, offset + ALIAS_PAGE_SIZE - 1)
                .execute()
            )

            if response.data is None:
                page: list[dict[str, Any]] = []
            elif not isinstance(response.data, list):
                raise RestaurantMenuResolverError(
                    code="INVALID_RESTAURANT_ALIAS_RESPONSE",
                    message="Restaurangens alias gav ett ogiltigt svar.",
                    status_code=502,
                )
            else:
                page = response.data

            for value in page:
                if not isinstance(value, dict):
                    raise RestaurantMenuResolverError(
                        code="INVALID_RESTAURANT_ALIAS_RESPONSE",
                        message=(
                            "Restaurangens alias innehåller ogiltiga "
                            "uppgifter."
                        ),
                        status_code=502,
                    )
                alias_id = str(value.get("id") or "")
                if not alias_id or alias_id in seen_alias_ids:
                    raise RestaurantMenuResolverError(
                        code="INVALID_RESTAURANT_ALIAS_RESPONSE",
                        message=(
                            "Restaurangens alias innehåller ett saknat "
                            "eller duplicerat id."
                        ),
                        status_code=502,
                    )
                seen_alias_ids.add(alias_id)
                rows.append(value)

            if len(page) < ALIAS_PAGE_SIZE:
                break
            offset += ALIAS_PAGE_SIZE
    except RestaurantMenuResolverError:
        raise
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

    aliases: list[dict[str, Any]] = []
    for value in rows:
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


def _load_approved_alias_overrides(
    context: ToolRestaurantContext,
    menu_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    configured = APPROVED_ALIAS_OVERRIDES.get(
        context.restaurant_slug,
        {},
    )
    aliases: list[dict[str, Any]] = []
    for alias, official_name in configured.items():
        matching_items = [
            item
            for item in menu_items
            if str(item.get("official_name") or "").strip()
            == official_name
        ]
        if len(matching_items) != 1:
            raise RestaurantMenuResolverError(
                code="APPROVED_ALIAS_TARGET_INVALID",
                message=(
                    "Ett godkänt alias saknar en entydig aktiv "
                    "menyprodukt."
                ),
                status_code=502,
            )
        aliases.append(
            {
                "restaurant_id": str(context.restaurant_id),
                "menu_item_id": str(matching_items[0]["id"]),
                "alias": alias,
                "normalized_alias": _normalize_spoken_text(alias),
                "alias_type": "spoken",
                "priority": 100,
            }
        )
    return aliases


def _clear_resolver_catalog_cache() -> None:
    with _catalog_cache_lock:
        _catalog_cache.clear()
        _phrase_cache.clear()


def _load_resolver_catalog(
    context: ToolRestaurantContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cache_key = str(context.restaurant_id)
    now = monotonic()
    with _catalog_cache_lock:
        cached = _catalog_cache.get(cache_key)
        if cached is not None and now - cached[0] < (
            RESOLVER_CATALOG_CACHE_TTL_SECONDS
        ):
            return cached[1], list(cached[2])

    with _catalog_load_lock:
        now = monotonic()
        with _catalog_cache_lock:
            cached = _catalog_cache.get(cache_key)
            if cached is not None and now - cached[0] < (
                RESOLVER_CATALOG_CACHE_TTL_SECONDS
            ):
                return cached[1], list(cached[2])

        menu_items = _load_active_menu_items(context.restaurant_id)
        active_item_ids = {
            str(_parse_menu_item_id(item.get("id")))
            for item in menu_items
        }
        aliases = _load_menu_item_aliases(
            context.restaurant_id,
            active_item_ids,
        )
        with _catalog_cache_lock:
            _catalog_cache[cache_key] = (
                monotonic(),
                menu_items,
                list(aliases),
            )
        return menu_items, aliases


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


def _entry_message(entry: dict[str, Any]) -> str | None:
    for field_name in ("message", "content", "text"):
        value = entry.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _variant_follow_up_protein(value: str) -> str | None:
    words = _normalize_spoken_text(value).split()
    while words and words[0] in VARIANT_FOLLOW_UP_FILLER_WORDS:
        words.pop(0)
    words = [APPROVED_PROTEIN_ALIASES.get(word, word) for word in words]

    modifier_start: int | None = None
    for index, word in enumerate(words):
        if word == "extra":
            modifier_start = index
            break
        if word == "lägg" and words[index : index + 2] == ["lägg", "till"]:
            modifier_start = index
            break

    primary_words = (
        words[:modifier_start]
        if modifier_start is not None
        else words
    )
    proteins = {
        word
        for word in primary_words
        if word in VERIFIED_PROTEIN_VARIANTS
    }
    if len(proteins) == 1:
        return next(iter(proteins))
    return None


def _referenced_variant_family_proteins(
    context: ToolRestaurantContext,
    follow_up: str,
    requests: list[
        tuple[
            str,
            str,
            list[dict[str, Any]],
            list[dict[str, Any]],
        ]
    ],
) -> dict[str, str]:
    """Map explicitly referenced pending families to one stated protein.

    For example, "den gröna ... kyckling och den röda ... biff" maps
    Gaeng Keowan to Kyckling and Gaeng Ped to Biff. A bare list of two
    proteins is intentionally left unresolved because its assignment is not
    deterministic.
    """

    words = [
        APPROVED_PROTEIN_ALIASES.get(word, word)
        for word in _normalize_spoken_text(follow_up).split()
    ]
    pending_keys = {
        _variant_request_key(context, request)
        for request in requests
        if not request[3]
    }
    references: list[tuple[int, str]] = []
    for family_key in pending_keys:
        for index, word in enumerate(words):
            if word in APPROVED_VARIANT_FAMILY_REFERENCE_WORDS.get(
                family_key,
                frozenset(),
            ):
                references.append((index, family_key))

    references.sort()
    if len(references) < 2:
        return {}

    assignments: dict[str, str] = {}
    for index, (start, family_key) in enumerate(references):
        end = (
            references[index + 1][0]
            if index + 1 < len(references)
            else len(words)
        )
        proteins = {
            word
            for word in words[start + 1 : end]
            if word in VERIFIED_PROTEIN_VARIANTS
        }
        if len(proteins) == 1:
            assignments[family_key] = next(iter(proteins))

    return assignments


def _pending_variant_history(
    entries: list[dict[str, Any]],
) -> tuple[str, list[str]] | None:
    latest_user_index: int | None = None
    for index in range(len(entries) - 1, -1, -1):
        role = str(entries[index].get("role") or "").casefold()
        if role in {"user", "customer"}:
            latest_user_index = index
            break
    if latest_user_index is None:
        return None

    latest_utterance = _entry_message(entries[latest_user_index])
    if latest_utterance is None:
        return None

    # Prefer the resolver's deterministic result over the agent's wording.
    # The agent may paraphrase a protein clarification (for example,
    # "Kan du repetera?"). An unresolved AMBIGUOUS tool result still proves
    # that the preceding order is waiting for a variant selection.
    ambiguous_result_index: int | None = None
    for index in range(latest_user_index - 1, -1, -1):
        entry = entries[index]
        tool_results = entry.get("tool_results")
        results = (
            [result for result in tool_results if isinstance(result, dict)]
            if isinstance(tool_results, list)
            else [entry]
        )
        statuses = {
            status
            for result in results
            if (status := _tool_result_status(result)) is not None
        }
        if "MATCH" in statuses or "NO_MATCH" in statuses:
            break
        if "AMBIGUOUS" in statuses:
            ambiguous_result_index = index
            continue

    if ambiguous_result_index is not None:
        original_user_index: int | None = None
        for index in range(ambiguous_result_index - 1, -1, -1):
            role = str(entries[index].get("role") or "").casefold()
            if role in {"user", "customer"}:
                original_user_index = index
                break
        if original_user_index is not None:
            original_utterance = _entry_message(
                entries[original_user_index]
            )
            if original_utterance is not None:
                follow_ups = [
                    message
                    for entry in entries[original_user_index + 1 :]
                    if str(entry.get("role") or "").casefold()
                    in {"user", "customer"}
                    and (message := _entry_message(entry)) is not None
                ]
                if follow_ups:
                    return original_utterance, follow_ups

    question_index: int | None = None
    for index in range(latest_user_index - 1, -1, -1):
        role = str(entries[index].get("role") or "").casefold()
        if role in {"agent", "assistant"}:
            agent_message = _entry_message(entries[index])
            if agent_message is None:
                continue
            if "vilket protein" not in _normalize_spoken_text(
                agent_message
            ):
                return None
            question_index = index
            break
        if role in {"user", "customer"}:
            return None
    if question_index is None:
        return None

    follow_ups = [latest_utterance]
    while True:
        prior_user_index: int | None = None
        prior_utterance: str | None = None
        for index in range(question_index - 1, -1, -1):
            role = str(entries[index].get("role") or "").casefold()
            if role not in {"user", "customer"}:
                continue
            prior_user_index = index
            prior_utterance = _entry_message(entries[index])
            break
        if prior_user_index is None or prior_utterance is None:
            return None

        previous_question_index: int | None = None
        for index in range(prior_user_index - 1, -1, -1):
            role = str(entries[index].get("role") or "").casefold()
            if role in {"user", "customer"}:
                break
            if role not in {"agent", "assistant"}:
                continue
            agent_message = _entry_message(entries[index])
            if agent_message is None:
                continue
            if "vilket protein" in _normalize_spoken_text(agent_message):
                previous_question_index = index
            break

        if previous_question_index is None:
            return prior_utterance, follow_ups

        follow_ups.insert(0, prior_utterance)
        question_index = previous_question_index


def _pending_sushi_utterance(
    entries: list[dict[str, Any]],
) -> str | None:
    latest_user_index: int | None = None
    for index in range(len(entries) - 1, -1, -1):
        role = str(entries[index].get("role") or "").casefold()
        if role in {"user", "customer"}:
            latest_user_index = index
            break
    if latest_user_index is None:
        return None

    latest_utterance = _entry_message(entries[latest_user_index])
    if latest_utterance is None:
        return None

    agent_index: int | None = None
    for index in range(latest_user_index - 1, -1, -1):
        role = str(entries[index].get("role") or "").casefold()
        if role in {"agent", "assistant"}:
            agent_index = index
            break
        if role in {"user", "customer"}:
            return None
    if agent_index is None:
        return None

    agent_message = _entry_message(entries[agent_index])
    normalized_agent_message = _normalize_spoken_text(agent_message or "")
    asks_for_sushi_size = (
        "hur många bitar" in normalized_agent_message
        and "sushi" in normalized_agent_message
    )
    asks_for_sushi_type = (
        "vanlig" in normalized_agent_message
        and "blanda egen" in normalized_agent_message
    )
    if not asks_for_sushi_size and not asks_for_sushi_type:
        return None

    if asks_for_sushi_type:
        sushi_size = _sushi_size_from_words(
            tuple(normalized_agent_message.split())
        )
        if sushi_size is None:
            return None
        return f"sushi {sushi_size} bitar {latest_utterance}"

    for index in range(agent_index - 1, -1, -1):
        role = str(entries[index].get("role") or "").casefold()
        if role not in {"user", "customer"}:
            continue
        prior_utterance = _entry_message(entries[index])
        if prior_utterance:
            return f"{prior_utterance} {latest_utterance}"
        return None
    return None


def _tool_result_status(result: dict[str, Any]) -> str | None:
    tool_name = str(
        result.get("tool_name")
        or result.get("name")
        or ""
    ).strip()
    if tool_name not in YZ_MENU_RESOLVER_TOOL_NAMES:
        return None

    value: object = (
        result.get("result_value")
        or result.get("result")
        or result.get("output")
    )
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None

    if not isinstance(value, dict):
        return None

    status = str(value.get("status") or "").upper()
    return (
        status
        if status in {"MATCH", "NO_MATCH", "AMBIGUOUS"}
        else None
    )


def _previous_unresolved_attempts(entries: list[dict[str, Any]]) -> int:
    attempts = 0
    for entry in entries:
        tool_results = entry.get("tool_results")
        results = (
            [result for result in tool_results if isinstance(result, dict)]
            if isinstance(tool_results, list)
            else [entry]
        )
        for result in results:
            status = _tool_result_status(result)
            if status in {"MATCH", "AMBIGUOUS"}:
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
            normalized = _normalize_match_text(item.get(field_name))
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
        normalized = _normalize_match_text(alias.get("alias"))
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


def _load_or_build_phrases(
    context: ToolRestaurantContext,
    menu_items: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
) -> list[_ResolverPhrase]:
    cache_key = str(context.restaurant_id)
    now = monotonic()
    with _catalog_cache_lock:
        cached = _phrase_cache.get(cache_key)
        if cached is not None and now - cached[0] < (
            RESOLVER_CATALOG_CACHE_TTL_SECONDS
        ):
            return cached[1]

    phrases = _build_phrases(menu_items, aliases)
    with _catalog_cache_lock:
        _phrase_cache[cache_key] = (now, phrases)
    return phrases


def _find_matches(
    utterance: str,
    phrases: list[_ResolverPhrase],
) -> tuple[list[_ResolverPhrase], bool]:
    words = tuple(_normalize_match_text(utterance).split())
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


def _contains_words(
    words: tuple[str, ...],
    phrase_words: tuple[str, ...],
) -> bool:
    width = len(phrase_words)
    return any(
        words[start : start + width] == phrase_words
        for start in range(0, len(words) - width + 1)
    )


def _contains_coordinated_family_words(
    words: tuple[str, ...],
    phrase_words: tuple[str, ...],
) -> bool:
    """Recognize a shared final word in a coordinated family request.

    Swedish callers naturally say "grön och röd curry". The word "curry"
    belongs to both color qualifiers even though it is spoken once. This is an
    exact structural match, not a fuzzy match: it only applies to two-word
    configured family phrases and requires the literal conjunction "och".
    """

    if len(phrase_words) != 2:
        return False

    qualifier, shared_word = phrase_words
    for start, word in enumerate(words):
        if word != qualifier:
            continue
        for end in range(start + 2, len(words)):
            if words[end] != shared_word:
                continue
            if words[start + 1 : end].count("och") == 1:
                return True
            break
    return False


def _unique_fuzzy_variant_family(
    utterance_words: tuple[str, ...],
    configured: dict[str, tuple[str, str]],
    excluded_spoken_families: frozenset[str],
    direct_matches: list[_ResolverPhrase],
) -> tuple[str, tuple[str, ...]] | None:
    """Return one high-confidence configured family match, or nothing.

    This operates only on approved variant-family aliases. It never
    fuzzy-matches the general menu, and it rejects ties instead of guessing.
    """

    comparable_words = tuple(
        word
        for word in utterance_words
        if word not in VARIANT_FAMILY_FILLER_WORDS
    )
    best_by_family: dict[str, tuple[float, str, tuple[str, ...]]] = {}

    for spoken_family_name, family_config in configured.items():
        normalized_spoken_family = _normalize_spoken_text(
            spoken_family_name
        )
        if normalized_spoken_family in excluded_spoken_families:
            continue

        spoken_words = tuple(normalized_spoken_family.split())
        width = len(spoken_words)
        if not width or width > len(comparable_words):
            continue
        if any(
            _contains_words(phrase.words, spoken_words)
            or _contains_words(spoken_words, phrase.words)
            for phrase in direct_matches
        ):
            continue

        best_ratio = 0.0
        for start in range(0, len(comparable_words) - width + 1):
            candidate = " ".join(
                comparable_words[start : start + width]
            )
            ratio = SequenceMatcher(
                None,
                candidate,
                normalized_spoken_family,
            ).ratio()
            best_ratio = max(best_ratio, ratio)

        if best_ratio < VARIANT_FAMILY_FUZZY_MINIMUM_RATIO:
            continue

        menu_family_name = _normalize_spoken_text(family_config[0])
        previous = best_by_family.get(menu_family_name)
        candidate = (
            best_ratio,
            normalized_spoken_family,
            spoken_words,
        )
        if previous is None or candidate > previous:
            best_by_family[menu_family_name] = candidate

    if not best_by_family:
        return None

    ranked = sorted(
        best_by_family.items(),
        key=lambda value: (-value[1][0], value[0]),
    )
    winning = ranked[0][1]
    if (
        len(ranked) > 1
        and winning[0] - ranked[1][1][0]
        < VARIANT_FAMILY_FUZZY_MINIMUM_MARGIN
    ):
        return None

    return winning[1], winning[2]


def _fuzzy_menu_phrase_words(
    phrase: _ResolverPhrase,
) -> tuple[str, ...]:
    """Return customer-spoken words without an optional menu number."""

    words = phrase.words
    return words[1:] if words[:1] and words[0].isdigit() else words


def _unique_fuzzy_menu_phrase(
    utterance: str,
    phrases: list[_ResolverPhrase],
) -> _ResolverPhrase | None:
    """Resolve one unambiguous high-confidence menu phrase, or nothing.

    This is deliberately a final fallback rather than a similarity search over
    the whole order. Exact names and approved aliases have already had their
    chance. The caller must also ensure no variant-family rule applies.
    """

    utterance_words = tuple(
        word
        for word in _normalize_match_text(utterance).split()
        if word not in MENU_FUZZY_FILLER_WORDS
    )
    if not utterance_words:
        return None

    best_by_item_id: dict[str, tuple[float, _ResolverPhrase]] = {}
    for phrase in phrases:
        phrase_words = _fuzzy_menu_phrase_words(phrase)
        if not phrase_words:
            continue
        if (
            len(phrase_words) == 1
            and len(phrase_words[0])
            < MENU_FUZZY_MINIMUM_SINGLE_WORD_LENGTH
        ):
            continue

        best_ratio = 0.0
        for width in range(
            max(1, len(phrase_words) - 1),
            min(len(utterance_words), len(phrase_words) + 1) + 1,
        ):
            for start in range(0, len(utterance_words) - width + 1):
                candidate = " ".join(utterance_words[start : start + width])
                ratio = SequenceMatcher(
                    None,
                    candidate,
                    " ".join(phrase_words),
                ).ratio()
                best_ratio = max(best_ratio, ratio)

        if best_ratio < MENU_FUZZY_MINIMUM_RATIO:
            continue

        item_id = str(_parse_menu_item_id(phrase.item.get("id")))
        previous = best_by_item_id.get(item_id)
        candidate = (best_ratio, phrase)
        if previous is None or candidate[0] > previous[0]:
            best_by_item_id[item_id] = candidate

    if not best_by_item_id:
        return None

    ranked = sorted(
        best_by_item_id.values(),
        key=lambda value: (-value[0], value[1].normalized_text),
    )
    winning_ratio, winning_phrase = ranked[0]
    if (
        len(ranked) > 1
        and winning_ratio - ranked[1][0] < MENU_FUZZY_MINIMUM_MARGIN
    ):
        return None

    return _ResolverPhrase(
        normalized_text=winning_phrase.normalized_text,
        words=winning_phrase.words,
        item=winning_phrase.item,
        source="fuzzy",
    )


def _sushi_size_from_words(words: tuple[str, ...]) -> int | None:
    sizes: set[int] = set()
    for index, word in enumerate(words):
        size = (
            YZ_SUSHI_SIZE_TOKENS.get(word)
            or YZ_SUSHI_COMPOUND_SIZE_TOKENS.get(word)
        )
        if size is None:
            continue
        if word in YZ_SUSHI_COMPOUND_SIZE_TOKENS:
            sizes.add(size)
            continue
        previous_word = words[index - 1] if index > 0 else ""
        next_word = words[index + 1] if index + 1 < len(words) else ""
        if (
            next_word in {"bitar", "bitars"}
            or previous_word == "sushi"
            or next_word == "sushi"
        ):
            sizes.add(size)
    return next(iter(sizes)) if len(sizes) == 1 else None


def _exact_active_item(
    menu_items: list[dict[str, Any]],
    official_name: str,
) -> dict[str, Any] | None:
    normalized_target = _normalize_spoken_text(official_name)
    matches = [
        item
        for item in menu_items
        if _normalize_spoken_text(item.get("official_name"))
        == normalized_target
    ]
    return matches[0] if len(matches) == 1 else None


def _resolved_match(
    item: dict[str, Any],
    matched_text: str,
) -> dict[str, Any]:
    return {
        "menu_item_id": _parse_menu_item_id(item.get("id")),
        "official_name": str(item.get("official_name") or "").strip(),
        "customer_display_name": str(
            item.get("customer_display_name")
            or item.get("official_name")
            or ""
        ).strip(),
        "matched_text": matched_text,
        "match_source": "canonical",
    }


def _yz_sushi_request(
    context: ToolRestaurantContext,
    utterance: str,
    menu_items: list[dict[str, Any]],
    direct_matches: list[_ResolverPhrase],
) -> dict[str, Any] | None:
    if context.restaurant_slug != "yz-thai-wok-sushi":
        return None

    words = tuple(_normalize_spoken_text(utterance).split())
    if "sushi" not in words:
        return None

    direct_sushi_matches = [
        phrase
        for phrase in direct_matches
        if "sushi" in _normalize_spoken_text(
            phrase.item.get("official_name")
        ).split()
    ]
    if direct_sushi_matches:
        if len(direct_sushi_matches) == 1 and _normalize_spoken_text(
            direct_sushi_matches[0].item.get("official_name")
        ).startswith("egenkomponerad sushi "):
            return {
                "status": "MATCH",
                "selected_item": direct_sushi_matches[0].item,
                "matched_text": direct_sushi_matches[0].normalized_text,
                "is_custom": True,
            }
        return None

    size = _sushi_size_from_words(words)
    if size is None:
        has_active_sushi_pair = any(
            _exact_active_item(menu_items, regular_name) is not None
            and _exact_active_item(
                menu_items,
                f"Egenkomponerad sushi – {sushi_size} bitar",
            )
            is not None
            for sushi_size, regular_name in YZ_SUSHI_REGULAR_NAMES.items()
        )
        if not has_active_sushi_pair:
            return None
        return {
            "status": "AMBIGUOUS",
            "customer_message": "Hur många bitar sushi vill du ha?",
            "matches": [],
        }

    regular_item = _exact_active_item(
        menu_items,
        YZ_SUSHI_REGULAR_NAMES[size],
    )
    custom_item = _exact_active_item(
        menu_items,
        f"Egenkomponerad sushi – {size} bitar",
    )
    if regular_item is None or custom_item is None:
        return None

    normalized_utterance = " ".join(words)
    wants_custom = any(
        phrase in normalized_utterance
        for phrase in (
            "blanda egen",
            "egenkomponerad",
            "egen sushi",
            "egna bitar",
        )
    )
    wants_regular = "vanlig" in words
    matched_text = f"sushi {size} bitar"

    if wants_custom != wants_regular:
        selected_item = custom_item if wants_custom else regular_item
        return {
            "status": "MATCH",
            "selected_item": selected_item,
            "matched_text": matched_text,
            "is_custom": wants_custom,
        }

    size_word = YZ_SUSHI_SIZE_WORDS[size]
    return {
        "status": "AMBIGUOUS",
        "customer_message": (
            f"Vill du ha vanlig {size_word}bitars sushi eller blanda egen?"
        ),
        "matches": [
            _resolved_match(regular_item, matched_text),
            _resolved_match(custom_item, matched_text),
        ],
    }


def _variant_family_request(
    context: ToolRestaurantContext,
    utterance: str,
    menu_items: list[dict[str, Any]],
    direct_matches: list[_ResolverPhrase],
    excluded_spoken_families: frozenset[str] = frozenset(),
) -> tuple[
    str,
    str,
    list[dict[str, Any]],
    list[dict[str, Any]],
] | None:
    configured = APPROVED_VARIANT_FAMILIES.get(
        context.restaurant_slug,
        {},
    )
    utterance_words = tuple(
        APPROVED_PROTEIN_ALIASES.get(word, word)
        for word in _normalize_spoken_text(utterance).split()
    )

    configured_families = sorted(
        configured.items(),
        key=lambda value: -len(
            _normalize_spoken_text(value[0]).split()
        ),
    )
    selected_family: tuple[str, tuple[str, ...], bool] | None = None
    for spoken_family_name, _ in configured_families:
        normalized_spoken_family = _normalize_spoken_text(
            spoken_family_name
        )
        if normalized_spoken_family in excluded_spoken_families:
            continue
        spoken_family_words = tuple(
            normalized_spoken_family.split()
        )
        if not (
            _contains_words(utterance_words, spoken_family_words)
            or _contains_coordinated_family_words(
                utterance_words,
                spoken_family_words,
            )
        ):
            continue
        if any(
            _contains_words(phrase.words, spoken_family_words)
            for phrase in direct_matches
        ):
            continue
        selected_family = (
            normalized_spoken_family,
            spoken_family_words,
            False,
        )
        break

    if selected_family is None:
        fuzzy_family = _unique_fuzzy_variant_family(
            utterance_words,
            configured,
            excluded_spoken_families,
            direct_matches,
        )
        if fuzzy_family is None:
            return None
        selected_family = (*fuzzy_family, True)

    normalized_spoken_family, spoken_family_words, fuzzy_match = (
        selected_family
    )
    menu_family_name, customer_message = configured[
        normalized_spoken_family
    ]
    normalized_menu_family = _normalize_spoken_text(menu_family_name)
    variants: list[dict[str, Any]] = []
    variants_by_protein: dict[str, dict[str, Any]] = {}
    for item in menu_items:
        normalized_names = {
            _normalize_spoken_text(item.get(field_name))
            for field_name in MENU_ITEM_NAME_FIELDS
        }
        normalized_names.discard("")
        is_family_item = False
        for normalized_name in normalized_names:
            if normalized_name == normalized_menu_family:
                is_family_item = True
                break
            prefix = f"{normalized_menu_family} "
            if not normalized_name.startswith(prefix):
                continue
            suffix_words = normalized_name[len(prefix) :].split()
            if suffix_words[:1] == ["med"]:
                suffix_words = suffix_words[1:]
            protein = " ".join(suffix_words)
            if protein in VERIFIED_PROTEIN_VARIANTS:
                is_family_item = True
                variants_by_protein[protein] = item
                break
        if is_family_item:
            variants.append(item)

    if not variants:
        return None

    selected_variants: list[dict[str, Any]] = []
    if fuzzy_match:
        protein = _variant_follow_up_protein(utterance)
        if protein in variants_by_protein:
            selected_variants.append(variants_by_protein[protein])
    else:
        for protein, item in variants_by_protein.items():
            spoken_forms = (
                (*spoken_family_words, protein),
                (*spoken_family_words, "med", protein),
                (protein, *spoken_family_words),
                (protein, "med", *spoken_family_words),
            )
            if any(
                _contains_words(utterance_words, form)
                for form in spoken_forms
            ):
                selected_variants.append(item)

    return (
        normalized_spoken_family,
        customer_message,
        variants,
        selected_variants,
    )


def _variant_family_requests(
    context: ToolRestaurantContext,
    utterance: str,
    menu_items: list[dict[str, Any]],
    direct_matches: list[_ResolverPhrase],
) -> list[
    tuple[
        str,
        str,
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]
]:
    requests: list[
        tuple[
            str,
            str,
            list[dict[str, Any]],
            list[dict[str, Any]],
        ]
    ] = []
    excluded: set[str] = set()
    configured = APPROVED_VARIANT_FAMILIES.get(
        context.restaurant_slug,
        {},
    )
    while True:
        request = _variant_family_request(
            context,
            utterance,
            menu_items,
            direct_matches,
            frozenset(excluded),
        )
        if request is None:
            return requests
        requests.append(request)
        selected_config = configured.get(request[0])
        if selected_config is None:
            excluded.add(request[0])
            continue
        selected_menu_family = _normalize_spoken_text(
            selected_config[0]
        )
        excluded.update(
            _normalize_spoken_text(spoken_family)
            for spoken_family, family_config in configured.items()
            if _normalize_spoken_text(family_config[0])
            == selected_menu_family
        )


def _variant_request_key(
    context: ToolRestaurantContext,
    request: tuple[
        str,
        str,
        list[dict[str, Any]],
        list[dict[str, Any]],
    ],
) -> str:
    configured = APPROVED_VARIANT_FAMILIES.get(
        context.restaurant_slug,
        {},
    )
    family_config = configured.get(request[0])
    return _normalize_spoken_text(
        family_config[0] if family_config is not None else request[0]
    )


def _selected_variant_for_protein(
    context: ToolRestaurantContext,
    request: tuple[
        str,
        str,
        list[dict[str, Any]],
        list[dict[str, Any]],
    ],
    protein: str,
    menu_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    synthetic_utterance = f"{request[0]} {protein}"
    candidates = _variant_family_requests(
        context,
        synthetic_utterance,
        menu_items,
        [],
    )
    request_key = _variant_request_key(context, request)
    for candidate in candidates:
        if (
            _variant_request_key(context, candidate) == request_key
            and candidate[3]
        ):
            return candidate[3]
    return []


def _apply_pending_variant_follow_ups(
    context: ToolRestaurantContext,
    base_requests: list[
        tuple[
            str,
            str,
            list[dict[str, Any]],
            list[dict[str, Any]],
        ]
    ],
    follow_ups: list[str],
    menu_items: list[dict[str, Any]],
) -> list[
    tuple[
        str,
        str,
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]
]:
    requests = list(base_requests)

    def request_indexes() -> dict[str, int]:
        return {
            _variant_request_key(context, request): index
            for index, request in enumerate(requests)
        }

    for follow_up in follow_ups:
        indexes = request_indexes()
        explicit_requests = _variant_family_requests(
            context,
            follow_up,
            menu_items,
            [],
        )
        explicit_keys: set[str] = set()
        for explicit_request in explicit_requests:
            key = _variant_request_key(context, explicit_request)
            explicit_keys.add(key)
            existing_index = indexes.get(key)
            if existing_index is None:
                requests.append(explicit_request)
                indexes[key] = len(requests) - 1
                continue
            if explicit_request[3]:
                requests[existing_index] = explicit_request

        referenced_proteins = _referenced_variant_family_proteins(
            context,
            follow_up,
            requests,
        )
        if referenced_proteins:
            indexes = request_indexes()
            for key, protein in referenced_proteins.items():
                index = indexes.get(key)
                if index is None:
                    continue
                request = requests[index]
                if request[3]:
                    continue
                selected_variants = _selected_variant_for_protein(
                    context,
                    request,
                    protein,
                    menu_items,
                )
                if selected_variants:
                    requests[index] = (
                        request[0],
                        request[1],
                        request[2],
                        selected_variants,
                    )

        protein = _variant_follow_up_protein(follow_up)
        if protein is None:
            continue

        indexes = request_indexes()
        unresolved_keys = {
            key
            for key, index in indexes.items()
            if not requests[index][3]
        }
        target_keys = (
            explicit_keys & unresolved_keys
            if explicit_keys
            else unresolved_keys
        )
        for key in target_keys:
            index = indexes[key]
            request = requests[index]
            selected_variants = _selected_variant_for_protein(
                context,
                request,
                protein,
                menu_items,
            )
            if selected_variants:
                requests[index] = (
                    request[0],
                    request[1],
                    request[2],
                    selected_variants,
                )

    return requests


def _append_selected_variant_matches(
    matches: list[_ResolverPhrase],
    family_requests: list[
        tuple[
            str,
            str,
            list[dict[str, Any]],
            list[dict[str, Any]],
        ]
    ],
) -> None:
    known_ids = {
        str(_parse_menu_item_id(match.item.get("id")))
        for match in matches
    }
    for family_name, _, _, selected_variants in family_requests:
        for item in selected_variants:
            item_id = str(_parse_menu_item_id(item.get("id")))
            if item_id in known_ids:
                continue
            known_ids.add(item_id)
            matches.append(
                _ResolverPhrase(
                    normalized_text=family_name,
                    words=tuple(family_name.split()),
                    item=item,
                    source="canonical",
                )
            )


def _single_match_customer_message(
    matches: list[dict[str, Any]],
    utterance: str,
) -> str | None:
    if len(matches) != 1:
        return None

    normalized_utterance = _normalize_spoken_text(utterance)
    if any(
        quantity in normalized_utterance.split()
        for quantity in ("två", "tre", "fyra", "fem")
    ):
        return None

    match = matches[0]
    official_name = re.sub(
        r"^\s*\d+\.\s*",
        "",
        str(match.get("official_name") or "").strip(),
    )
    family_parts = re.split(r"\s+[–-]\s+", official_name, maxsplit=1)
    family = family_parts[0]
    protein = family_parts[1] if len(family_parts) == 2 else None
    normalized_family = _normalize_spoken_text(family)

    if official_name == "Satay Gai":
        customer_name = "kycklingspett med jordnötssås"
        article = "ett"
    elif protein and normalized_family == "pad med mamuang":
        customer_name = f"{protein.casefold()} med cashewnötter"
        article = ""
    elif protein and normalized_family == "gaeng ped" and (
        "röd curry" in normalized_utterance
    ):
        customer_name = f"röd curry med {protein.casefold()}"
        article = "en"
    elif protein and normalized_family == "pad thai":
        customer_name = f"Pad Thai med {protein.casefold()}"
        article = "en"
    else:
        customer_name = re.sub(
            r"^\s*\d+\.\s*",
            "",
            str(match.get("customer_display_name") or official_name).strip(),
        )
        article = "en"

    normalized_protein = _normalize_spoken_text(protein or "")
    extra_shrimp = (
        normalized_protein != "räkor"
        and "räkor" in normalized_utterance.split()
        and (
            "extra räkor" in normalized_utterance
            or "lägg till räkor" in normalized_utterance
            or (
                normalized_protein
                and f"{normalized_protein} och räkor"
                in normalized_utterance
            )
        )
    )
    if extra_shrimp:
        customer_name = f"{customer_name} och extra räkor"

    article_prefix = f"{article} " if article else ""
    return (
        f"Okej perfekt, {article_prefix}{customer_name}. "
        "Har jag fått med allting?"
    )


def resolve_restaurant_menu_items(
    *,
    context: ToolRestaurantContext,
    request: ResolveMenuItemsV2Request,
) -> dict[str, Any]:
    entries = _history_entries(request)
    utterance = _latest_user_utterance(entries)
    try:
        menu_items, aliases = _load_resolver_catalog(context)
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

    aliases.extend(
        _load_approved_alias_overrides(context, menu_items)
    )
    phrases = _load_or_build_phrases(context, menu_items, aliases)
    pending_variant_utterance: str | None = None
    pending_variant_context = _pending_variant_history(entries)
    if pending_variant_context is not None:
        original_utterance, follow_ups = pending_variant_context
        original_matches, _ = _find_matches(
            original_utterance,
            phrases,
        )
        original_family_requests = _variant_family_requests(
            context,
            original_utterance,
            menu_items,
            original_matches,
        )
    else:
        original_utterance = ""
        follow_ups = []
        original_matches = []
        original_family_requests = []

    if original_family_requests:
        pending_variant_utterance = " ".join(
            [original_utterance, *follow_ups]
        )
        family_utterance = pending_variant_utterance
        matches = []
        known_phrases: set[tuple[str, str]] = set()
        for contextual_utterance in [original_utterance, *follow_ups]:
            contextual_matches, _ = _find_matches(
                contextual_utterance,
                phrases,
            )
            for phrase in contextual_matches:
                key = (
                    str(_parse_menu_item_id(phrase.item.get("id"))),
                    phrase.normalized_text,
                )
                if key in known_phrases:
                    continue
                known_phrases.add(key)
                matches.append(phrase)
        family_requests = _apply_pending_variant_follow_ups(
            context,
            original_family_requests,
            follow_ups,
            menu_items,
        )
    else:
        matches, _ = _find_matches(utterance, phrases)
        family_utterance = utterance
        family_requests = _variant_family_requests(
            context,
            family_utterance,
            menu_items,
            matches,
        )
        if (
            not matches
            and not family_requests
            and "sushi" not in _normalize_spoken_text(utterance).split()
        ):
            fuzzy_phrase = _unique_fuzzy_menu_phrase(utterance, phrases)
            if fuzzy_phrase is not None:
                matches = [fuzzy_phrase]

    _append_selected_variant_matches(matches, family_requests)
    family_request = next(
        (request for request in family_requests if not request[3]),
        None,
    )

    family_is_waiting_for_variant = (
        family_request is not None and not family_request[3]
    )
    sushi_utterance = (
        pending_variant_utterance
        if pending_variant_utterance is not None
        and "sushi" in _normalize_spoken_text(
            pending_variant_utterance
        ).split()
        else family_utterance
    )
    sushi_request = None
    if not family_is_waiting_for_variant:
        sushi_request = _yz_sushi_request(
            context,
            sushi_utterance,
            menu_items,
            matches,
        )
        if sushi_request is None:
            pending_sushi_utterance = _pending_sushi_utterance(entries)
            if pending_sushi_utterance is not None:
                sushi_utterance = pending_sushi_utterance
                sushi_request = _yz_sushi_request(
                    context,
                    sushi_utterance,
                    menu_items,
                    matches,
                )
    if sushi_request is not None:
        direct_match_values = [
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
        ]
        if family_request is not None:
            family_name = family_request[0]
            known_ids = {
                str(match["menu_item_id"])
                for match in direct_match_values
            }
            for item in family_request[3]:
                family_match = _resolved_match(item, family_name)
                if str(family_match["menu_item_id"]) not in known_ids:
                    direct_match_values.append(family_match)
                    known_ids.add(str(family_match["menu_item_id"]))
        if sushi_request["status"] == "MATCH":
            selected_match = _resolved_match(
                sushi_request["selected_item"],
                sushi_request["matched_text"],
            )
            known_ids = {
                str(match["menu_item_id"])
                for match in direct_match_values
            }
            if str(selected_match["menu_item_id"]) not in known_ids:
                direct_match_values.append(selected_match)
            customer_message = None
            if not sushi_request["is_custom"]:
                customer_message = _single_match_customer_message(
                    direct_match_values,
                    sushi_utterance,
                )
            return {
                "success": True,
                "status": "MATCH",
                "action": "continue",
                "unresolved_attempt": 0,
                "stop_recovery": False,
                "customer_message": customer_message,
                "required_agent_action": (
                    "say_customer_message_exactly"
                    if customer_message
                    else "accept_matches_without_variant_questions"
                ),
                "all_required_variants_resolved": True,
                "matches": direct_match_values,
            }

        known_ids = {
            str(match["menu_item_id"])
            for match in direct_match_values
        }
        direct_match_values.extend(
            match
            for match in sushi_request["matches"]
            if str(match["menu_item_id"]) not in known_ids
        )
        return {
            "success": True,
            "status": "AMBIGUOUS",
            "action": "clarify",
            "unresolved_attempt": 0,
            "stop_recovery": False,
            "customer_message": sushi_request["customer_message"],
            "required_agent_action": "say_customer_message_exactly",
            "all_required_variants_resolved": False,
            "matches": direct_match_values,
        }

    if family_request is not None:
        (
            family_name,
            customer_message,
            variants,
            selected_variants,
        ) = family_request
        if selected_variants:
            resolved_matches: list[tuple[int, dict[str, Any]]] = []
            utterance_words = tuple(
                _normalize_spoken_text(utterance).split()
            )

            def match_position(phrase_words: tuple[str, ...]) -> int:
                width = len(phrase_words)
                for start in range(
                    0,
                    len(utterance_words) - width + 1,
                ):
                    if (
                        utterance_words[start : start + width]
                        == phrase_words
                    ):
                        return start
                return len(utterance_words)

            seen_item_ids: set[str] = set()
            for phrase in matches:
                item_id = str(_parse_menu_item_id(phrase.item.get("id")))
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
                resolved_matches.append(
                    (
                        match_position(phrase.words),
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
                        },
                    )
                )

            family_position = match_position(
                tuple(family_name.split())
            )
            for item in selected_variants:
                item_id = str(_parse_menu_item_id(item.get("id")))
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
                resolved_matches.append(
                    (
                        family_position,
                        {
                            "menu_item_id": _parse_menu_item_id(
                                item.get("id")
                            ),
                            "official_name": str(
                                item.get("official_name") or ""
                            ).strip(),
                            "customer_display_name": str(
                                item.get("customer_display_name")
                                or item.get("official_name")
                                or ""
                            ).strip(),
                            "matched_text": family_name,
                            "match_source": "canonical",
                        },
                    )
                )
            resolved_matches.sort(key=lambda value: value[0])
            match_values = [value[1] for value in resolved_matches]
            customer_message = _single_match_customer_message(
                match_values,
                family_utterance,
            )
            return {
                "success": True,
                "status": "MATCH",
                "action": "continue",
                "unresolved_attempt": 0,
                "stop_recovery": False,
                "customer_message": customer_message,
                "required_agent_action": (
                    "say_customer_message_exactly"
                    if customer_message
                    else "accept_matches_without_variant_questions"
                ),
                "all_required_variants_resolved": True,
                "matches": match_values,
            }
        direct_match_values = [
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
        ]
        return {
            "success": True,
            "status": "AMBIGUOUS",
            "action": "clarify",
            "unresolved_attempt": 0,
            "stop_recovery": False,
            "customer_message": customer_message,
            "required_agent_action": "say_customer_message_exactly",
            "all_required_variants_resolved": False,
            "matches": direct_match_values,
        }

    if matches:
        match_values = [
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
        ]
        customer_message = _single_match_customer_message(
            match_values,
            family_utterance,
        )
        return {
            "success": True,
            "status": "MATCH",
            "action": "continue",
            "unresolved_attempt": 0,
            "stop_recovery": False,
            "customer_message": customer_message,
            "required_agent_action": (
                "say_customer_message_exactly"
                if customer_message
                else "accept_matches_without_variant_questions"
            ),
            "all_required_variants_resolved": True,
            "matches": match_values,
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
