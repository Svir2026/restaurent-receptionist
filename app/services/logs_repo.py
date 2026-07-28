from __future__ import annotations

import logging
from typing import Any

from app.services.supabase_client import call_logs_insert

logger = logging.getLogger(__name__)

LOGS_COLUMNS: list[str] = [
    "logged_at",
    "webhook_type",
    "conversation_id",
    "agent_id",
    "status",
    "duration_secs",
    "caller_number",
    "has_audio",
    "has_user_audio",
    "has_response_audio",
    "transcript_text",
    "payload_json",
]


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    return str(v)


class LogsRepository:

    def append_log_row(self, row: dict[str, Any]) -> None:
        db_row = {h: _safe_str(row.get(h)) for h in LOGS_COLUMNS}
        call_logs_insert(db_row)
