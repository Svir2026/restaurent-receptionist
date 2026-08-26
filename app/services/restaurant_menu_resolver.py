from __future__ import annotations

import json
import logging
import re
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
    YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME,
)
from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)

YZ_MENU_RESOLVER_TOOL_NAME = YZ_TEST_MENU_RESOLVER_V2_TOOL_NAME

APPROVED_ALIAS_OVERRIDES = {
    "yz-thai-wok-sushi": {
        "yakinaki": "24. Yakiniku",
        "kycklingspett": "23. Satay Gai",
        "kycklingpsett": "23. Satay Gai",
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
        "gaeng ped": (
            "gaeng ped",
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

RESOLVER_CATALOG_CACHE_TTL_SECONDS = 30.0
ALIAS_PAGE_SIZE = 1000
_catalog_cache: dict[
    str,
    tuple[float, list[dict[str, Any]], list[dict[str, Any]]],
] = {}
_catalog_cache_lock = Lock()

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
        _catalog_cache[cache_key] = (now, menu_items, list(aliases))
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


def _pending_variant_utterance(
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
    if agent_message is None or "vilket protein" not in (
        _normalize_spoken_text(agent_message)
    ):
        return None

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
    if tool_name != YZ_MENU_RESOLVER_TOOL_NAME:
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


def _contains_words(
    words: tuple[str, ...],
    phrase_words: tuple[str, ...],
) -> bool:
    width = len(phrase_words)
    return any(
        words[start : start + width] == phrase_words
        for start in range(0, len(words) - width + 1)
    )


def _variant_family_request(
    context: ToolRestaurantContext,
    utterance: str,
    menu_items: list[dict[str, Any]],
    direct_matches: list[_ResolverPhrase],
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
    utterance_words = tuple(_normalize_spoken_text(utterance).split())

    for spoken_family_name, family_config in configured.items():
        menu_family_name, customer_message = family_config
        normalized_spoken_family = _normalize_spoken_text(
            spoken_family_name
        )
        spoken_family_words = tuple(
            normalized_spoken_family.split()
        )
        if not _contains_words(
            utterance_words,
            spoken_family_words,
        ):
            continue

        if any(
            _contains_words(phrase.words, spoken_family_words)
            for phrase in direct_matches
        ):
            continue

        normalized_menu_family = _normalize_spoken_text(
            menu_family_name
        )

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
            continue

        selected_variants: list[dict[str, Any]] = []
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

        if selected_variants:
            return (
                normalized_spoken_family,
                customer_message,
                variants,
                selected_variants,
            )

        protein_was_supplied = any(
            protein in utterance_words
            for protein in variants_by_protein
        )
        if protein_was_supplied:
            continue

        return (
            normalized_spoken_family,
            customer_message,
            variants,
            [],
        )

    return None


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
    matches, _ = _find_matches(utterance, phrases)

    family_utterance = utterance
    family_request = _variant_family_request(
        context,
        family_utterance,
        menu_items,
        matches,
    )
    if family_request is None:
        pending_utterance = _pending_variant_utterance(entries)
        if pending_utterance is not None:
            family_utterance = pending_utterance
            family_request = _variant_family_request(
                context,
                family_utterance,
                menu_items,
                matches,
            )
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
        return {
            "success": True,
            "status": "AMBIGUOUS",
            "action": "clarify",
            "unresolved_attempt": 0,
            "stop_recovery": False,
            "customer_message": customer_message,
            "matches": [
                {
                    "menu_item_id": _parse_menu_item_id(item.get("id")),
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
                }
                for item in variants
            ],
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
            utterance,
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
