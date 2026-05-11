from __future__ import annotations

MATCH_SUFFIX_LEN = 10


def normalize_phone(raw: str) -> str:
    """Strip whitespace, drop a single leading '+', require at least one digit."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("phone number is required")
    if s.startswith("+"):
        s = s[1:].strip()
    if not any(c.isdigit() for c in s):
        raise ValueError("phone number has no digits")
    return s


def digits_only(raw: str | None) -> str:
    return "".join(c for c in (raw or "") if c.isdigit())


def phone_suffix_match(a: str | None, b: str | None) -> bool:
    """Same caller if the last MATCH_SUFFIX_LEN digit sequences match."""
    da = digits_only(a)
    db = digits_only(b)
    if not da or not db:
        return False
    return da[-MATCH_SUFFIX_LEN:] == db[-MATCH_SUFFIX_LEN:]
