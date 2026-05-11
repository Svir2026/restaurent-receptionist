from __future__ import annotations

import phonenumbers

from app.core.config import settings


def _parse_region_list(csv: str | None) -> list[str]:
    if not csv:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in csv.split(","):
        p = part.strip().upper()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _parse_order_for_phone(
    *,
    default_region: str | None,
) -> list[str | None]:
    """Regions to try in order, ending with None (international / no default)."""
    primary = (default_region if default_region is not None else settings.phone_default_region) or ""
    primary = primary.strip().upper() or None
    fallbacks = _parse_region_list(settings.phone_fallback_regions)

    regions: list[str | None] = []
    seen: set[str] = set()
    if primary:
        seen.add(primary)
        regions.append(primary)
    for r in fallbacks:
        if r not in seen:
            seen.add(r)
            regions.append(r)
    regions.append(None)
    return regions


def try_normalize_phone(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return normalize_phone(str(raw))
    except ValueError:
        return None


def normalize_phone(raw: str, *, default_region: str | None = None) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("phone number is required")

    attempts: list[tuple[str, str | None]] = []
    for reg in _parse_order_for_phone(default_region=default_region):
        attempts.append((raw, reg))

    digits = "".join(c for c in raw if c.isdigit())
    # GET query decoding often turns '+' into a space; callers may send full country code digits only.
    if "+" not in raw and len(digits) >= 11:
        attempts.append(("+" + digits, None))

    last_exc: phonenumbers.NumberParseException | None = None
    for s, reg in attempts:
        try:
            parsed = phonenumbers.parse(s, reg)
            if phonenumbers.is_possible_number(parsed) and phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException as e:
            last_exc = e
            continue
    msg = f"invalid phone number: {last_exc}" if last_exc else "invalid phone number"
    raise ValueError(msg) from last_exc
