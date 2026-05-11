from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_bool_str(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return ""


def _get_data(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _conversation_id(body: dict[str, Any], data: dict[str, Any]) -> str:
    for src in (data, body):
        for key in ("conversation_id", "conversationId", "id"):
            v = src.get(key) if isinstance(src, dict) else None
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def _agent_id(data: dict[str, Any]) -> str:
    for key in ("agent_id", "agentId"):
        v = data.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _status(data: dict[str, Any]) -> str:
    for key in ("status", "conversation_status", "state", "call_status"):
        v = data.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _duration_secs(data: dict[str, Any]) -> str:
    for key in ("duration_secs", "call_duration_secs", "call_duration", "duration"):
        v = data.get(key)
        if v is None:
            continue
        try:
            if isinstance(v, (int, float)):
                return str(int(v)) if v == int(v) else str(v)
            s = str(v).strip()
            if s:
                return str(int(float(s))) if "." in s else s
        except (TypeError, ValueError):
            continue
    meta = data.get("metadata")
    if isinstance(meta, dict):
        for key in ("call_duration_secs", "duration_secs", "duration"):
            v = meta.get(key)
            if v is not None:
                try:
                    return str(int(float(v)))
                except (TypeError, ValueError):
                    pass
    return ""


def _caller_number(data: dict[str, Any]) -> str:
    meta = data.get("metadata")
    if isinstance(meta, dict):
        for key in ("caller_number", "phone_number", "from_number", "caller_id", "phone", "from"):
            v = meta.get(key)
            if v is not None and str(v).strip():
                return str(v).strip()
    for dv_key in ("dynamic_variables", "conversation_initiation_client_data"):
        dv = data.get(dv_key)
        if isinstance(dv, dict):
            for key in ("system__caller_id", "caller_id", "caller_number", "phone", "phone_number"):
                v = dv.get(key)
                if v is not None and str(v).strip():
                    return str(v).strip()
    return ""


def _flatten_transcript(data: dict[str, Any]) -> str:
    for key in ("transcript", "transcripts", "messages"):
        t = data.get(key)
        if not isinstance(t, list) or not t:
            continue
        lines: list[str] = []
        for item in t:
            if isinstance(item, dict):
                role = (item.get("role") or item.get("type") or item.get("source") or "").strip()
                msg = (
                    item.get("message")
                    or item.get("text")
                    or item.get("content")
                    or item.get("utterance")
                    or ""
                )
                if isinstance(msg, dict):
                    msg = str(msg)
                msg = str(msg).strip() if msg else ""
                if msg:
                    if role:
                        lines.append(f"{role}: {msg}")
                    else:
                        lines.append(msg)
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
        if lines:
            return "\n".join(lines)
    return ""


_MAX_CELL = 45000
_TRUNC_SUFFIX = "...<truncated>"


def _truncate_cell(s: str, max_len: int = _MAX_CELL) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - len(_TRUNC_SUFFIX)] + _TRUNC_SUFFIX


def _truncate_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _truncate_cell(raw)


def build_log_row(body: dict[str, Any]) -> dict[str, Any]:
    data = _get_data(body)
    wh_type = body.get("type")
    webhook_type = str(wh_type).strip() if wh_type is not None else ""

    transcript = _truncate_cell(_flatten_transcript(data))

    return {
        "logged_at": _iso_now(),
        "webhook_type": webhook_type,
        "conversation_id": _conversation_id(body, data),
        "agent_id": _agent_id(data),
        "status": _status(data),
        "duration_secs": _duration_secs(data),
        "caller_number": _caller_number(data),
        "has_audio": _as_bool_str(data.get("has_audio")),
        "has_user_audio": _as_bool_str(data.get("has_user_audio")),
        "has_response_audio": _as_bool_str(data.get("has_response_audio")),
        "transcript_text": transcript,
        "payload_json": _truncate_json(body),
    }
