from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


LIBANON_RESTAURANT_ID = "613079d4-7680-40b0-a5cc-465e813a5267"
LIBANON_RESTAURANT_SLUG = "lebanon-kolgrill"
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "libanon_menu_candidate.json"
)

FUZZY_MIN_SCORE = 0.86
FUZZY_MIN_MARGIN = 0.08
FUZZY_MIN_CHARACTERS = 5


class LibanonCatalogError(RuntimeError):
    pass


def normalize_spoken_text(value: object) -> str:
    """Normalize Swedish speech text without translating its meaning."""

    if value is None:
        return ""

    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    normalized = normalized.replace("&", " och ")
    normalized = re.sub(r"[^0-9a-zåäöéèü]+", " ", normalized)
    return " ".join(normalized.split())


@dataclass(frozen=True)
class CatalogOption:
    source_key: str
    name: str
    kitchen_name: str
    price_delta_minor: int
    is_default: bool
    aliases: tuple[str, ...]

    @property
    def phrases(self) -> tuple[str, ...]:
        values = [self.name, *self.aliases]
        normalized_name = normalize_spoken_text(self.name)

        if normalized_name.startswith("med "):
            values.append(normalized_name[4:])

        return tuple(
            dict.fromkeys(
                normalized
                for value in values
                if (normalized := normalize_spoken_text(value))
            )
        )


@dataclass(frozen=True)
class CatalogOptionGroup:
    source_key: str
    catalog_group_source_key: str
    name: str
    group_type: str
    selection_mode: str
    is_required: bool
    min_select: int
    max_select: int
    prerequisite_option_source_keys: tuple[str, ...]
    options: tuple[CatalogOption, ...]


@dataclass(frozen=True)
class CatalogItem:
    source_key: str
    category_source_key: str
    category_name: str
    official_name: str
    customer_display_name: str
    kitchen_display_name: str
    description: str | None
    item_type: str
    base_price_minor: int
    currency: str
    aliases: tuple[str, ...]
    option_groups: tuple[CatalogOptionGroup, ...]
    price_verification_status: str

    @property
    def is_pizza(self) -> bool:
        return "pizza" in normalize_spoken_text(self.category_name)

    @property
    def phrases(self) -> tuple[tuple[str, str], ...]:
        values = [(self.official_name, "canonical")]
        values.extend((alias, "alias") for alias in self.aliases)
        result: list[tuple[str, str]] = []
        seen: set[str] = set()

        for value, source in values:
            normalized = normalize_spoken_text(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append((normalized, source))

        return tuple(result)


@dataclass(frozen=True)
class CatalogMention:
    item: CatalogItem
    matched_text: str
    match_source: str
    start: int
    end: int


@dataclass(frozen=True)
class CatalogAmbiguity:
    matched_text: str
    items: tuple[CatalogItem, ...]


@dataclass(frozen=True)
class FuzzySuggestion:
    item: CatalogItem
    matched_text: str
    score: float
    margin: float


@dataclass(frozen=True)
class LibanonCatalog:
    verification_status: str
    source: dict[str, Any]
    items: tuple[CatalogItem, ...]
    phrase_index: dict[str, tuple[tuple[CatalogItem, str], ...]]
    item_index: dict[str, CatalogItem]

    def find_exact_mentions(
        self,
        utterance: str,
    ) -> tuple[tuple[CatalogMention, ...], tuple[CatalogAmbiguity, ...]]:
        normalized = normalize_spoken_text(utterance)
        candidates: list[CatalogMention] = []
        ambiguities: list[CatalogAmbiguity] = []

        for phrase, indexed_matches in self.phrase_index.items():
            pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
            for match in re.finditer(pattern, normalized):
                unique_items = {
                    indexed.item.source_key: indexed.item
                    for indexed in (
                        CatalogMention(
                            item=item,
                            matched_text=phrase,
                            match_source=source,
                            start=match.start(),
                            end=match.end(),
                        )
                        for item, source in indexed_matches
                    )
                }

                if len(unique_items) > 1:
                    ambiguities.append(
                        CatalogAmbiguity(
                            matched_text=phrase,
                            items=tuple(unique_items.values()),
                        )
                    )
                    continue

                item, source = indexed_matches[0]
                candidates.append(
                    CatalogMention(
                        item=item,
                        matched_text=phrase,
                        match_source=source,
                        start=match.start(),
                        end=match.end(),
                    )
                )

        candidates.sort(
            key=lambda value: (
                -(value.end - value.start),
                value.start,
                value.item.source_key,
            )
        )

        accepted: list[CatalogMention] = []
        occupied: list[tuple[int, int]] = []
        for candidate in candidates:
            if any(
                candidate.start < end and candidate.end > start
                for start, end in occupied
            ):
                continue
            accepted.append(candidate)
            occupied.append((candidate.start, candidate.end))

        accepted.sort(key=lambda value: value.start)
        return tuple(accepted), tuple(ambiguities)

    def suggest_unique_fuzzy(
        self,
        utterance: str,
    ) -> FuzzySuggestion | None:
        normalized = normalize_spoken_text(utterance)
        words = normalized.split()
        if not words:
            return None

        phrases = tuple(self.phrase_index)
        max_phrase_words = max((len(value.split()) for value in phrases), default=1)
        windows: set[str] = set()

        for size in range(1, min(len(words), max_phrase_words + 2) + 1):
            for start in range(0, len(words) - size + 1):
                window = " ".join(words[start : start + size])
                if len(window) >= FUZZY_MIN_CHARACTERS:
                    windows.add(window)

        scored_by_item: dict[str, tuple[float, CatalogItem, str]] = {}
        for phrase in phrases:
            if len(phrase) < FUZZY_MIN_CHARACTERS:
                continue
            best_window = max(
                windows,
                key=lambda value: SequenceMatcher(None, value, phrase).ratio(),
                default="",
            )
            if not best_window:
                continue
            score = SequenceMatcher(None, best_window, phrase).ratio()
            for item, _ in self.phrase_index[phrase]:
                current = scored_by_item.get(item.source_key)
                if current is None or score > current[0]:
                    scored_by_item[item.source_key] = (score, item, best_window)

        if not scored_by_item:
            return None

        scored = sorted(
            scored_by_item.values(), key=lambda value: value[0], reverse=True
        )
        best_score, best_item, best_window = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        margin = best_score - runner_up

        if best_score < FUZZY_MIN_SCORE or margin < FUZZY_MIN_MARGIN:
            return None

        return FuzzySuggestion(
            item=best_item,
            matched_text=best_window,
            score=best_score,
            margin=margin,
        )

    def get_item(self, source_key: str) -> CatalogItem:
        try:
            return self.item_index[source_key]
        except KeyError as error:
            raise LibanonCatalogError(
                f"Unknown Libanon menu source key: {source_key}"
            ) from error


def _parse_option(raw: dict[str, Any]) -> CatalogOption:
    price_delta_minor = raw.get("price_delta_minor")
    if not isinstance(price_delta_minor, int):
        raise LibanonCatalogError("Option price must use integer minor units")

    return CatalogOption(
        source_key=str(raw["source_key"]),
        name=str(raw["name"]),
        kitchen_name=str(raw["kitchen_name"]),
        price_delta_minor=price_delta_minor,
        is_default=bool(raw.get("is_default")),
        aliases=tuple(str(value) for value in raw.get("aliases", [])),
    )


def _parse_group(raw: dict[str, Any]) -> CatalogOptionGroup:
    return CatalogOptionGroup(
        source_key=str(raw["source_key"]),
        catalog_group_source_key=str(raw["catalog_group_source_key"]),
        name=str(raw["name"]),
        group_type=str(raw["group_type"]),
        selection_mode=str(raw["selection_mode"]),
        is_required=bool(raw["is_required"]),
        min_select=int(raw["min_select"]),
        max_select=int(raw["max_select"]),
        prerequisite_option_source_keys=tuple(
            str(value) for value in raw.get("prerequisite_option_source_keys", [])
        ),
        options=tuple(_parse_option(value) for value in raw["options"]),
    )


def _parse_item(raw: dict[str, Any]) -> CatalogItem:
    base_price_minor = raw.get("base_price_minor")
    if not isinstance(base_price_minor, int) or base_price_minor < 0:
        raise LibanonCatalogError("Item price must use non-negative minor units")

    metadata = raw.get("metadata") or {}
    return CatalogItem(
        source_key=str(raw["source_key"]),
        category_source_key=str(raw["category_source_key"]),
        category_name=str(metadata["category_name"]),
        official_name=str(raw["official_name"]),
        customer_display_name=str(raw["customer_display_name"]),
        kitchen_display_name=str(raw["kitchen_display_name"]),
        description=(str(raw["description"]) if raw.get("description") else None),
        item_type=str(raw["item_type"]),
        base_price_minor=base_price_minor,
        currency=str(raw["currency"]),
        aliases=tuple(str(value) for value in raw.get("aliases", [])),
        option_groups=tuple(_parse_group(value) for value in raw["option_groups"]),
        price_verification_status=str(
            metadata.get("price_verification_status", "unverified")
        ),
    )


def _build_phrase_index(
    items: Iterable[CatalogItem],
) -> dict[str, tuple[tuple[CatalogItem, str], ...]]:
    result: dict[str, list[tuple[CatalogItem, str]]] = {}
    for item in items:
        for phrase, source in item.phrases:
            result.setdefault(phrase, []).append((item, source))
    return {key: tuple(value) for key, value in result.items()}


def load_libanon_catalog(
    path: Path = DEFAULT_CATALOG_PATH,
) -> LibanonCatalog:
    raw = json.loads(path.read_text(encoding="utf-8"))

    if raw.get("restaurant_id") != LIBANON_RESTAURANT_ID:
        raise LibanonCatalogError("Catalog restaurant_id is not Libanon")
    if raw.get("restaurant_slug") != LIBANON_RESTAURANT_SLUG:
        raise LibanonCatalogError("Catalog restaurant_slug is not Libanon")
    if raw.get("currency") != "SEK":
        raise LibanonCatalogError("Libanon catalog currency must be SEK")

    items = tuple(
        _parse_item(value) for value in raw.get("items", []) if value.get("is_active")
    )
    if not items:
        raise LibanonCatalogError("Libanon catalog contains no active items")

    item_index = {item.source_key: item for item in items}
    if len(item_index) != len(items):
        raise LibanonCatalogError("Libanon catalog has duplicate source keys")

    return LibanonCatalog(
        verification_status=str(raw.get("verification_status", "unverified")),
        source=dict(raw.get("source") or {}),
        items=items,
        phrase_index=_build_phrase_index(items),
        item_index=item_index,
    )


@lru_cache(maxsize=1)
def get_libanon_catalog() -> LibanonCatalog:
    return load_libanon_catalog()
