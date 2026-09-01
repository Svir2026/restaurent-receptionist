from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from app.schemas.libanon_order_engine import (
    LibanonOrderLine,
    LibanonOrderNote,
    LibanonOrderState,
    LibanonOrderTurnRequest,
    LibanonOrderTurnResponse,
    LibanonPendingQuestion,
    LibanonSelectedOption,
)
from app.services.libanon_menu_catalog import (
    LIBANON_RESTAURANT_ID,
    CatalogItem,
    CatalogMention,
    CatalogOption,
    CatalogOptionGroup,
    LibanonCatalog,
    get_libanon_catalog,
    normalize_spoken_text,
)
from app.services.voice_order_state import (
    VoiceOrderStateRepository,
    build_voice_order_event_id,
)


UNKNOWN_MESSAGES = (
    "Ursäkta, kan du repetera vilken rätt du ville ha?",
    "Tyvärr, vi har inte det på menyn. Vill du ha något annat?",
    "Det verkar vara lite tekniska problem. Du får gärna komma in och beställa.",
)

AFFIRMATIVE = {
    "ja",
    "ja tack",
    "ja precis",
    "ja det stämmer",
    "ja det blir bra",
    "japp",
    "yes",
    "absolut",
    "det blir bra",
    "det är bra",
    "det var allt",
    "det är allt",
    "bra så",
    "stämmer",
    "korrekt",
}

NEGATIVE = {
    "nej",
    "nej tack",
    "inte än",
}

QUANTITY_WORDS = {
    "en": 1,
    "ett": 1,
    "två": 2,
    "tva": 2,
    "tre": 3,
    "fyra": 4,
    "fem": 5,
    "sex": 6,
    "sju": 7,
    "åtta": 8,
    "atta": 8,
    "nio": 9,
    "tio": 10,
}

CUSTOMER_ITEM_NAMES = {
    "Favorite": "kycklingpizza med curry",
    "Kebab Pizza": "kebabpizza",
    "Shish Taouk": "kycklingspett",
    "Shish Kafta": "köttfärsspett",
    "Lamm Kafta": "lammfärsspett",
    "Vitlökssås (I Burk)": "vitlökssås",
    "Coca-Cola Original Taste 33 cl": "Cola",
    "Coca-Cola Zero Sugar 33 cl": "Cola Zero",
    "Fanta Orange 33cl - Fanta": "Fanta",
}

REMOVE_MARKERS = (
    "utan",
    "ingen",
    "inga",
    "skippa",
    "inte",
)

EXTRA_MARKERS = (
    "extra",
    "lägg till",
    "plus",
)

SIZE_WORDS = {
    "large": {"large", "familjepizza", "familje pizza", "stor"},
    "base": {"small", "standard", "normal", "vanlig", "medium"},
}


def _latest_user_utterance(
    request: LibanonOrderTurnRequest,
) -> tuple[str, int]:
    history = request.conversation_history
    assert isinstance(history, list)

    for index in range(len(history) - 1, -1, -1):
        entry = history[index]
        role = normalize_spoken_text(entry.get("role") or entry.get("source"))
        if role not in {"user", "customer"}:
            continue

        for field_name in ("message", "text", "content", "transcript"):
            value = entry.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip(), index

        content = entry.get("content")
        if isinstance(content, list):
            parts = [
                str(value.get("text") or "").strip()
                for value in content
                if isinstance(value, dict)
            ]
            combined = " ".join(value for value in parts if value)
            if combined:
                return combined, index

    raise ValueError("conversation_history has no user utterance")


def _new_state(conversation_id: str) -> LibanonOrderState:
    return LibanonOrderState(
        restaurant_id=LIBANON_RESTAURANT_ID,
        conversation_id=conversation_id,
    )


def _is_affirmative(value: str) -> bool:
    normalized = normalize_spoken_text(value)
    return normalized in AFFIRMATIVE


def _is_negative(value: str) -> bool:
    normalized = normalize_spoken_text(value)
    return normalized in NEGATIVE


def _customer_item_name(item: CatalogItem) -> str:
    return CUSTOMER_ITEM_NAMES.get(item.official_name, item.customer_display_name)


def _quantity_before_mention(
    normalized_utterance: str,
    mention: CatalogMention,
) -> int:
    prefix = normalized_utterance[: mention.start].strip()
    if not prefix:
        return 1

    quantity_pattern = "|".join(
        sorted((re.escape(value) for value in QUANTITY_WORDS), key=len, reverse=True)
    )
    match = re.search(
        rf"(?<!\w)(\d+|{quantity_pattern})(?:\s+(?:stycken|styck|st))?\s*$",
        prefix,
    )
    if match is None:
        return 1

    value = match.group(1)
    if value.isdigit():
        return max(1, min(int(value), 100))
    return QUANTITY_WORDS.get(value, 1)


def _option_phrase_matches(option: CatalogOption, utterance: str) -> bool:
    normalized = normalize_spoken_text(utterance)
    words = set(normalized.split())

    for phrase in option.phrases:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized):
            return True

    option_name = normalize_spoken_text(option.name)
    if "pommes" in words and "pommes" in option_name:
        return True
    if "ris" in words and "ris" in option_name:
        return True
    if "bulgur" in words and "bulgur" in option_name:
        return True
    if "klyftpotatis" in words and "klyftpotatis" in option_name:
        return True
    if "nöt" in words and "nötfärsspett" in option_name:
        return True
    if "lamm" in words and "lamm" in option_name:
        return True

    return False


def _matching_options(
    group: CatalogOptionGroup,
    utterance: str,
) -> list[CatalogOption]:
    return [
        option for option in group.options if _option_phrase_matches(option, utterance)
    ]


def _is_positive_addon_mention(
    option: CatalogOption,
    utterance: str,
) -> bool:
    normalized = normalize_spoken_text(utterance)
    option_phrases = option.phrases

    for phrase in option_phrases:
        if any(
            re.search(
                rf"(?<!\w){re.escape(marker)}\s+{re.escape(phrase)}(?!\w)",
                normalized,
            )
            for marker in ("extra", "lägg till", "plus", "med")
        ):
            return True

    return False


def _selected_option(
    group: CatalogOptionGroup,
    option: CatalogOption,
) -> LibanonSelectedOption:
    return LibanonSelectedOption(
        group_source_key=group.source_key,
        option_source_key=option.source_key,
        group_name=group.name,
        name=option.name,
        kitchen_name=option.kitchen_name,
        price_delta_minor=option.price_delta_minor,
    )


def _size_option(
    group: CatalogOptionGroup,
    utterance: str,
) -> CatalogOption | None:
    normalized = normalize_spoken_text(utterance)

    if any(value in normalized for value in SIZE_WORDS["large"]):
        for option in group.options:
            option_name = normalize_spoken_text(option.name)
            if "familj" in option_name or "large" in option_name:
                return option

    if any(value in normalized for value in SIZE_WORDS["base"]):
        for option in group.options:
            option_name = normalize_spoken_text(option.name)
            if option_name in {"standard", "medium"}:
                return option

    return next((value for value in group.options if value.is_default), None)


def _required_option_prompt(group: CatalogOptionGroup) -> str:
    normalized = normalize_spoken_text(group.name)
    option_names = [normalize_spoken_text(value.name) for value in group.options]

    if normalized == "tillbehör" and any(
        "klyftpotatis" in value for value in option_names
    ):
        return "Vill du ha ris, bulgur, pommes eller klyftpotatis?"
    if normalized == "tillbehör":
        return "Vill du ha pommes, ris eller bulgur?"
    if normalized == "kött":
        return "Vill du ha nötfärsspett eller lammfärsspett?"
    if normalized == "sås":
        return "Vilken sås vill du ha?"
    if normalized == "dryck":
        return "Vilken dryck vill du ha?"
    if normalized == "meze":
        return "Vilka sex meze vill du ha?"

    joined = ", ".join(value.name for value in group.options)
    return f"Vilket val vill du ha: {joined}?"


def _group_is_active(
    group: CatalogOptionGroup,
    selected_options: Iterable[LibanonSelectedOption],
) -> bool:
    prerequisites = set(group.prerequisite_option_source_keys)
    if not prerequisites:
        return True
    selected = {value.option_source_key for value in selected_options}
    return bool(prerequisites & selected)


def _split_modifier_values(
    segment: str,
    markers: tuple[str, ...],
) -> list[str]:
    normalized = normalize_spoken_text(segment)
    target_markers = set(markers)
    all_markers = sorted(
        {*REMOVE_MARKERS, *EXTRA_MARKERS},
        key=len,
        reverse=True,
    )
    marker_pattern = re.compile(
        rf"(?<!\w)({'|'.join(re.escape(value) for value in all_markers)})(?!\w)"
    )
    matches = list(marker_pattern.finditer(normalized))
    values: list[str] = []
    for index, match in enumerate(matches):
        if match.group(1) not in target_markers:
            continue
        end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        )
        clause = normalized[match.end() : end].strip(" ,.\t")
        clause = re.sub(r"^(?:och\s+)+", "", clause)
        clause = re.sub(r"\s+(?:också|pa den|på den)$", "", clause).strip()
        for value in re.split(r"\s+och\s+|\s*,\s*", clause):
            value = value.strip(" ,.")
            if value:
                values.append(value)
    return values


def _notes_from_segment(
    segment: str,
    *,
    selected_options: Iterable[LibanonSelectedOption],
) -> list[LibanonOrderNote]:
    selected_names = {normalize_spoken_text(value.name) for value in selected_options}
    notes: list[LibanonOrderNote] = []

    for value in _split_modifier_values(segment, REMOVE_MARKERS):
        notes.append(LibanonOrderNote(kind="remove", text=f"Utan {value}"))

    for value in _split_modifier_values(segment, EXTRA_MARKERS):
        if any(value == selected or value in selected for selected in selected_names):
            continue
        notes.append(LibanonOrderNote(kind="extra", text=f"Extra {value}"))

    normalized = normalize_spoken_text(segment)
    all_markers = "|".join(
        sorted(
            (re.escape(value) for value in (*REMOVE_MARKERS, *EXTRA_MARKERS)),
            key=len,
            reverse=True,
        )
    )
    half_and_half_match = re.search(
        r"(?:halva|hälften)\s+(.+?)(?:\s+och)?\s+" r"(?:halva|hälften)\s+(.+)$",
        normalized,
    )

    for match in re.finditer(
        rf"(?:^|\s)med\s+(?!extra\b)(.+?)(?=(?:\s+och\s+)?(?:{all_markers})\s+|$)",
        normalized,
    ):
        value = match.group(1).strip(" ,.")
        if not value:
            continue
        if half_and_half_match and value == half_and_half_match.group(0):
            continue
        if any(value == selected or value in selected for selected in selected_names):
            continue
        notes.append(LibanonOrderNote(kind="extra", text=f"Extra {value}"))

    if half_and_half_match:
        first_half = half_and_half_match.group(1).strip(" ,.")
        second_half = re.sub(
            r"\s+(?:också|pa den|på den)$",
            "",
            half_and_half_match.group(2),
        ).strip(" ,.")
        notes.append(
            LibanonOrderNote(
                kind="instruction",
                text=f"Halva {first_half}, halva {second_half}",
            )
        )

    unique: dict[tuple[str, str], LibanonOrderNote] = {}
    for note in notes:
        unique[(note.kind, normalize_spoken_text(note.text))] = note
    return list(unique.values())


def _question_for_group(
    *,
    line_id: str,
    group: CatalogOptionGroup,
) -> LibanonPendingQuestion:
    return LibanonPendingQuestion(
        question_id=str(uuid4()),
        kind="required_option",
        prompt=_required_option_prompt(group),
        line_id=line_id,
        group_source_key=group.source_key,
        candidate_option_source_keys=[value.source_key for value in group.options],
    )


def _build_order_line(
    *,
    mention: CatalogMention,
    quantity: int,
    segment: str,
    leading_context: str = "",
) -> tuple[LibanonOrderLine, list[LibanonPendingQuestion]]:
    item = mention.item
    selected: list[LibanonSelectedOption] = []
    pending: list[LibanonPendingQuestion] = []
    line_id = str(uuid4())

    for group in item.option_groups:
        if group.group_type == "size":
            option = _size_option(group, f"{leading_context} {segment}")
            if option is not None:
                selected.append(_selected_option(group, option))

    for group in item.option_groups:
        if group.group_type == "size" or not _group_is_active(group, selected):
            continue

        matched = _matching_options(group, segment)
        if group.group_type == "addon":
            if normalize_spoken_text(group.name) == "glutenfri":
                matched = [
                    value
                    for value in matched
                    if "glutenfri" in normalize_spoken_text(segment)
                ]
            else:
                matched = [
                    value
                    for value in matched
                    if _is_positive_addon_mention(value, segment)
                ]
        if group.selection_mode == "single" and len(matched) > 1:
            matched = []
        if group.max_select:
            matched = matched[: group.max_select]

        for option in matched:
            selected.append(_selected_option(group, option))

        if group.is_required and len(matched) < group.min_select:
            pending.append(_question_for_group(line_id=line_id, group=group))

    modifier_segment = segment
    matched_phrase = normalize_spoken_text(mention.matched_text)
    if matched_phrase:
        modifier_segment = re.sub(
            rf"(?<!\w){re.escape(matched_phrase)}(?!\w)",
            " ",
            normalize_spoken_text(segment),
            count=1,
        )
    notes = _notes_from_segment(modifier_segment, selected_options=selected)
    pricing_complete = not any(
        value.kind in {"extra", "instruction"} for value in notes
    )

    return (
        LibanonOrderLine(
            line_id=line_id,
            item_source_key=item.source_key,
            official_name=item.official_name,
            customer_display_name=_customer_item_name(item),
            kitchen_display_name=item.kitchen_display_name,
            category_name=item.category_name,
            quantity=quantity,
            base_price_minor=item.base_price_minor,
            currency=item.currency,
            selected_options=selected,
            notes=notes,
            price_verification_status=item.price_verification_status,
            pricing_complete=pricing_complete,
        ),
        pending,
    )


def _append_line(
    state: LibanonOrderState,
    line: LibanonOrderLine,
) -> LibanonOrderLine:
    state.items.append(line)
    return line


def _format_note(note: LibanonOrderNote) -> str:
    text = note.text
    if note.kind == "remove" and text.casefold().startswith("utan "):
        return "utan " + text[5:]
    if note.kind == "extra" and text.casefold().startswith("extra "):
        return "med extra " + text[6:]
    if note.kind == "instruction" and text.casefold().startswith("halva "):
        return "med " + text.casefold().replace(", halva ", " och halva ", 1)
    return text.casefold()


def _format_line(line: LibanonOrderLine, *, include_quantity: bool = True) -> str:
    if include_quantity:
        prefix = "en" if line.quantity == 1 else str(line.quantity)
        value = f"{prefix} {line.customer_display_name}"
    else:
        value = line.customer_display_name

    visible_options = []
    for option in line.selected_options:
        normalized = normalize_spoken_text(option.name)
        if normalized in {"standard", "medium"}:
            continue
        if normalize_spoken_text(option.group_name) == "storlek":
            visible_options.append(option.name.casefold())
        elif normalize_spoken_text(option.group_name) != "dryck":
            visible_options.append("med " + option.name.removeprefix("Med ").casefold())

    modifiers = [*visible_options, *(_format_note(value) for value in line.notes)]
    if modifiers:
        value += " " + " och ".join(modifiers)
    return value


def _join_spoken(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " och " + values[-1]


def _delta_confirmation(
    delta: list[LibanonOrderLine],
    *,
    had_existing_items: bool,
) -> str:
    spoken = _join_spoken([_format_line(value) for value in delta])
    if had_existing_items:
        return f"Okej, {spoken} också. Har jag fått med allting nu?"
    return f"Okej, {spoken}. Är det bra så?"


def _full_confirmation(state: LibanonOrderState) -> str:
    spoken = _join_spoken([_format_line(value) for value in state.items])
    return f"Okej perfekt, {spoken}. Testbeställningen är komplett."


def _find_group(
    *,
    catalog: LibanonCatalog,
    line: LibanonOrderLine,
    group_source_key: str,
) -> CatalogOptionGroup:
    item = catalog.get_item(line.item_source_key)
    for group in item.option_groups:
        if group.source_key == group_source_key:
            return group
    raise ValueError("Pending option group is not attached to the order item")


def _handle_pending_question(
    *,
    state: LibanonOrderState,
    catalog: LibanonCatalog,
    utterance: str,
) -> tuple[bool, list[LibanonOrderLine]]:
    if not state.pending_questions:
        return False, []

    pending = state.pending_questions[0]
    normalized = normalize_spoken_text(utterance)

    if pending.kind == "fuzzy_confirmation":
        if _is_affirmative(utterance) and pending.fuzzy_item_source_key:
            item = catalog.get_item(pending.fuzzy_item_source_key)
            mention = CatalogMention(
                item=item,
                matched_text=normalized,
                match_source="fuzzy_confirmed",
                start=0,
                end=len(normalized),
            )
            line, questions = _build_order_line(
                mention=mention,
                quantity=1,
                segment=pending.original_utterance or utterance,
            )
            state.pending_questions.pop(0)
            stored = _append_line(state, line)
            state.pending_questions[0:0] = questions
            state.unresolved_attempts = 0
            return True, [stored]
        if _is_negative(utterance):
            state.pending_questions.pop(0)
            return True, []
        return False, []

    if pending.kind == "catalog_ambiguity":
        candidates = [
            catalog.get_item(value) for value in pending.candidate_item_source_keys
        ]
        matched = [
            item
            for item in candidates
            if any(
                re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized)
                for phrase, _ in item.phrases
            )
        ]
        if len(matched) != 1:
            matched = [
                item
                for item in candidates
                if any(
                    re.search(
                        rf"(?<!\w){re.escape(value)}(?!\w)",
                        normalized,
                    )
                    for value in _disambiguation_terms(item)
                )
            ]
        if len(matched) == 1:
            item = matched[0]
            mention = CatalogMention(
                item=item,
                matched_text=normalized,
                match_source="canonical",
                start=0,
                end=len(normalized),
            )
            line, questions = _build_order_line(
                mention=mention,
                quantity=1,
                segment=utterance,
            )
            state.pending_questions.pop(0)
            stored = _append_line(state, line)
            state.pending_questions[0:0] = questions
            state.unresolved_attempts = 0
            return True, [stored]
        return False, []

    if pending.kind == "required_option":
        line = next(
            (value for value in state.items if value.line_id == pending.line_id),
            None,
        )
        if line is None or pending.group_source_key is None:
            raise ValueError("Pending question points to a missing order line")

        group = _find_group(
            catalog=catalog,
            line=line,
            group_source_key=pending.group_source_key,
        )
        matched = _matching_options(group, utterance)
        if group.selection_mode == "single" and len(matched) != 1:
            return False, []
        if group.selection_mode == "multiple" and not matched:
            return False, []

        existing = {value.option_source_key for value in line.selected_options}
        for option in matched:
            if option.source_key not in existing:
                line.selected_options.append(_selected_option(group, option))

        selected_count = sum(
            1
            for value in line.selected_options
            if value.group_source_key == group.source_key
        )
        if selected_count >= group.min_select:
            state.pending_questions.pop(0)
        else:
            remaining = group.min_select - selected_count
            pending.prompt = f"Vilka {remaining} meze till vill du ha?"

        delta_lines = [line]
        mentions, ambiguities = catalog.find_exact_mentions(utterance)
        if not ambiguities:
            segments = _segments_for_mentions(
                normalize_spoken_text(utterance),
                mentions,
            )
            for mention, segment in zip(mentions, segments):
                if mention.item.source_key == line.item_source_key:
                    continue
                added, questions = _build_order_line(
                    mention=mention,
                    quantity=_quantity_before_mention(
                        normalize_spoken_text(utterance), mention
                    ),
                    segment=segment,
                )
                delta_lines.append(_append_line(state, added))
                state.pending_questions.extend(questions)

        state.unresolved_attempts = 0
        return True, delta_lines

    return False, []


def _ambiguous_question(items: Iterable[CatalogItem]) -> str:
    values = list(items)
    shared_names = len(
        {normalize_spoken_text(_customer_item_name(value)) for value in values}
    ) != len(values) or len(
        {normalize_spoken_text(value.kitchen_display_name) for value in values}
    ) != len(
        values
    )
    names = [
        _disambiguation_label(value) if shared_names else _customer_item_name(value)
        for value in values
    ]
    return f"Menar du {_join_spoken(names)}?"


def _disambiguation_label(item: CatalogItem) -> str:
    name = _customer_item_name(item)
    category = normalize_spoken_text(item.category_name)
    if category == "dryckesmenyer":
        return f"{name} med dryck"
    if category == "mixspett":
        return f"ordinarie {name}"
    if category == "kalla meze":
        return f"{name} som meze"
    if category == "tillbehör och sås":
        return f"{name} som tillbehör"
    return f"{name} från {item.category_name}"


def _disambiguation_terms(item: CatalogItem) -> tuple[str, ...]:
    category = normalize_spoken_text(item.category_name)
    if category == "dryckesmenyer":
        return ("med dryck", "dryckesmeny", "lunchmeny")
    if category == "mixspett":
        return ("ordinarie",)
    if category == "kalla meze":
        return ("meze", "kalla meze")
    if category == "tillbehör och sås":
        return ("tillbehör", "sås", "burk")
    return (category,)


def _segments_for_mentions(
    normalized_utterance: str,
    mentions: tuple[CatalogMention, ...],
) -> list[str]:
    segments = []
    for index, mention in enumerate(mentions):
        end = (
            mentions[index + 1].start
            if index + 1 < len(mentions)
            else len(normalized_utterance)
        )
        segment = normalized_utterance[mention.start : end].strip()
        if index + 1 < len(mentions):
            segment = re.sub(
                r"(?:\s+och|\s+samt)?\s+(?:en|ett|två|tre|fyra|\d+)\s*$",
                "",
                segment,
            ).strip()
        segments.append(segment)
    return segments


def _is_existing_line_modification(
    *,
    normalized_utterance: str,
    mention: CatalogMention,
) -> bool:
    prefix = normalized_utterance[: mention.start].strip()
    has_modifier = any(
        re.search(rf"(?<!\w){re.escape(value)}(?!\w)", normalized_utterance)
        for value in (*REMOVE_MARKERS, *EXTRA_MARKERS)
    )
    introduces_new_quantity = bool(
        re.search(r"(?:^|\s)(?:en|ett|två|tre|fyra|\d+)\s*$", prefix)
    )
    return has_modifier and not introduces_new_quantity


def _apply_modification_to_existing(
    *,
    state: LibanonOrderState,
    mention: CatalogMention,
    segment: str,
) -> LibanonOrderLine | None:
    line = next(
        (
            value
            for value in reversed(state.items)
            if value.item_source_key == mention.item.source_key
        ),
        None,
    )
    if line is None:
        return None

    item = mention.item
    for group in item.option_groups:
        if group.group_type != "addon":
            continue
        matched = _matching_options(group, segment)
        if normalize_spoken_text(group.name) == "glutenfri":
            matched = [
                value
                for value in matched
                if "glutenfri" in normalize_spoken_text(segment)
            ]
        else:
            matched = [
                value for value in matched if _is_positive_addon_mention(value, segment)
            ]
        existing = {value.option_source_key for value in line.selected_options}
        for option in matched:
            if option.source_key not in existing:
                line.selected_options.append(_selected_option(group, option))

    new_notes = _notes_from_segment(segment, selected_options=line.selected_options)
    existing_notes = {
        (value.kind, normalize_spoken_text(value.text)) for value in line.notes
    }
    for note in new_notes:
        key = (note.kind, normalize_spoken_text(note.text))
        if key not in existing_notes:
            line.notes.append(note)
            if note.kind in {"extra", "instruction"}:
                line.pricing_complete = False

    return line


def _response(
    *,
    action: str,
    say: str,
    event_id: str,
    state: LibanonOrderState,
    cart_changed: bool,
    delta_lines: list[LibanonOrderLine],
    catalog: LibanonCatalog,
) -> LibanonOrderTurnResponse:
    order_ready = state.status == "ready_to_submit"
    submission_allowed = (
        order_ready
        and catalog.verification_status == "verified"
        and all(
            value.price_verification_status == "verified" and value.pricing_complete
            for value in state.items
        )
    )
    blocked_reason = None
    if order_ready and not submission_allowed:
        blocked_reason = (
            "Libanons katalogpriser är ännu inte restaurangverifierade. "
            "Testordern får inte skickas till produktion."
        )

    return LibanonOrderTurnResponse(
        success=True,
        action=action,
        say=say,
        event_id=event_id,
        idempotent_replay=False,
        state_revision=state.revision,
        cart_changed=cart_changed,
        order_ready=order_ready,
        submission_allowed=submission_allowed,
        submission_blocked_reason=blocked_reason,
        delta_lines=delta_lines,
        cart=state.items,
        pending_questions=state.pending_questions,
    )


def process_libanon_order_turn(
    *,
    request: LibanonOrderTurnRequest,
    repository: VoiceOrderStateRepository,
    catalog: LibanonCatalog | None = None,
) -> LibanonOrderTurnResponse:
    """Apply one customer turn to the authoritative Libanon order state."""

    catalog = catalog or get_libanon_catalog()
    utterance, entry_index = _latest_user_utterance(request)
    event_id = build_voice_order_event_id(
        conversation_id=request.conversation_id,
        entry_index=entry_index,
        utterance=utterance,
    )

    replay = repository.find_event(
        restaurant_id=LIBANON_RESTAURANT_ID,
        conversation_id=request.conversation_id,
        event_id=event_id,
    )
    if replay is not None:
        return replay.model_copy(update={"idempotent_replay": True})

    state = repository.load(
        restaurant_id=LIBANON_RESTAURANT_ID,
        conversation_id=request.conversation_id,
    ) or _new_state(request.conversation_id)
    expected_revision = state.revision

    if state.status == "stopped":
        say = UNKNOWN_MESSAGES[2]
        action = "technical_stop"
        delta: list[LibanonOrderLine] = []
        cart_changed = False
    else:
        pending_handled, delta = _handle_pending_question(
            state=state,
            catalog=catalog,
            utterance=utterance,
        )
        cart_changed = bool(delta)
        repeat_pending = False

        if not pending_handled and state.pending_questions:
            pending_mentions, pending_ambiguities = catalog.find_exact_mentions(
                utterance
            )
            has_menu_match = bool(pending_mentions or pending_ambiguities)
            pending = state.pending_questions[0]
            if has_menu_match and pending.kind in {
                "catalog_ambiguity",
                "fuzzy_confirmation",
            }:
                state.pending_questions.pop(0)
            elif not has_menu_match:
                repeat_pending = True

        if pending_handled and state.pending_questions:
            state.status = "collecting"
            say = state.pending_questions[0].prompt
            action = "ask_question"
        elif pending_handled and delta:
            state.status = "awaiting_confirmation"
            say = _delta_confirmation(
                delta,
                had_existing_items=len(state.items) > len(delta),
            )
            action = "confirm_delta"
        elif pending_handled:
            state.status = "collecting"
            say = "Vilken rätt vill du ha istället?"
            action = "no_change"
            delta = []
            cart_changed = False
        elif repeat_pending:
            state.status = "collecting"
            say = state.pending_questions[0].prompt
            action = "ask_question"
            delta = []
            cart_changed = False
        elif state.status == "awaiting_confirmation" and _is_affirmative(utterance):
            state.status = "ready_to_submit"
            state.unresolved_attempts = 0
            say = _full_confirmation(state)
            action = "confirm_full_order"
            delta = []
            cart_changed = False
        elif state.status == "awaiting_confirmation" and _is_negative(utterance):
            state.status = "collecting"
            say = "Vad vill du ändra eller lägga till?"
            action = "no_change"
            delta = []
            cart_changed = False
        else:
            normalized = normalize_spoken_text(utterance)
            mentions, ambiguities = catalog.find_exact_mentions(utterance)

            if ambiguities:
                ambiguity = ambiguities[0]
                state.pending_questions.append(
                    LibanonPendingQuestion(
                        question_id=str(uuid4()),
                        kind="catalog_ambiguity",
                        prompt=_ambiguous_question(ambiguity.items),
                        candidate_item_source_keys=[
                            value.source_key for value in ambiguity.items
                        ],
                    )
                )
                state.status = "collecting"
                state.unresolved_attempts = 0
                say = state.pending_questions[0].prompt
                action = "ask_question"
                delta = []
                cart_changed = False
            elif mentions:
                had_existing_items = bool(state.items)
                delta = []
                new_questions: list[LibanonPendingQuestion] = []
                segments = _segments_for_mentions(normalized, mentions)

                for index, (mention, segment) in enumerate(zip(mentions, segments)):
                    previous_end = mentions[index - 1].end if index else 0
                    between = normalized[previous_end : mention.start]
                    leading_context = re.split(
                        r"\b(?:och|samt)\b",
                        between,
                    )[-1]
                    if _is_existing_line_modification(
                        normalized_utterance=normalized,
                        mention=mention,
                    ):
                        modified = _apply_modification_to_existing(
                            state=state,
                            mention=mention,
                            segment=segment,
                        )
                        if modified is not None:
                            delta.append(modified)
                            continue

                    line, questions = _build_order_line(
                        mention=mention,
                        quantity=_quantity_before_mention(normalized, mention),
                        segment=segment,
                        leading_context=leading_context,
                    )
                    delta.append(_append_line(state, line))
                    new_questions.extend(questions)

                state.pending_questions.extend(new_questions)
                state.unresolved_attempts = 0
                cart_changed = bool(delta)

                if state.pending_questions:
                    state.status = "collecting"
                    say = state.pending_questions[0].prompt
                    action = "ask_question"
                else:
                    state.status = "awaiting_confirmation"
                    say = _delta_confirmation(
                        delta,
                        had_existing_items=had_existing_items,
                    )
                    action = "confirm_delta"
            else:
                # A modifier-only follow-up belongs to the last order line.
                modifier_only = any(
                    re.search(rf"(?<!\w){re.escape(value)}(?!\w)", normalized)
                    for value in (*REMOVE_MARKERS, *EXTRA_MARKERS)
                )
                if modifier_only and state.items:
                    line = state.items[-1]
                    item = catalog.get_item(line.item_source_key)
                    synthetic = CatalogMention(
                        item=item,
                        matched_text="",
                        match_source="context",
                        start=0,
                        end=0,
                    )
                    modified = _apply_modification_to_existing(
                        state=state,
                        mention=synthetic,
                        segment=normalized,
                    )
                    delta = [modified] if modified is not None else []
                    state.status = "awaiting_confirmation"
                    state.unresolved_attempts = 0
                    say = _delta_confirmation(delta, had_existing_items=True)
                    action = "confirm_delta"
                    cart_changed = bool(delta)
                else:
                    fuzzy = catalog.suggest_unique_fuzzy(utterance)
                    if fuzzy is not None:
                        state.pending_questions.append(
                            LibanonPendingQuestion(
                                question_id=str(uuid4()),
                                kind="fuzzy_confirmation",
                                prompt=f"Menar du {_customer_item_name(fuzzy.item)}?",
                                fuzzy_item_source_key=fuzzy.item.source_key,
                                original_utterance=utterance,
                            )
                        )
                        state.status = "collecting"
                        state.unresolved_attempts = 0
                        say = state.pending_questions[0].prompt
                        action = "ask_question"
                        delta = []
                        cart_changed = False
                    else:
                        state.unresolved_attempts = min(
                            state.unresolved_attempts + 1,
                            3,
                        )
                        attempt = state.unresolved_attempts
                        say = UNKNOWN_MESSAGES[attempt - 1]
                        action = (
                            "repeat_unknown_item"
                            if attempt == 1
                            else (
                                "reject_unknown_item"
                                if attempt == 2
                                else "technical_stop"
                            )
                        )
                        state.status = "stopped" if attempt == 3 else "collecting"
                        delta = []
                        cart_changed = False

    state.revision = expected_revision + 1
    state.updated_at = datetime.now(timezone.utc)
    state.processed_event_ids = [
        *state.processed_event_ids[-99:],
        event_id,
    ]
    response = _response(
        action=action,
        say=say,
        event_id=event_id,
        state=state,
        cart_changed=cart_changed,
        delta_lines=delta,
        catalog=catalog,
    )
    saved = repository.save_transition(
        expected_revision=expected_revision,
        state=state,
        event_id=event_id,
        utterance=utterance,
        response=response,
    )
    return saved.response.model_copy(
        update={"idempotent_replay": saved.idempotent_replay}
    )
