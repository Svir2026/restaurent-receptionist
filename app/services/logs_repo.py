from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.sheets_client import SheetsClient, get_sheets_client
from app.services.supabase_sync import call_logs_insert

logger = logging.getLogger(__name__)

_header_verified: set[tuple[str, str]] = set()

LOGS_HEADERS: list[str] = [
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


@dataclass(frozen=True)
class LogsRepository:
    client: SheetsClient
    spreadsheet_id: str
    tab: str

    @classmethod
    def from_settings(cls) -> "LogsRepository":
        client = get_sheets_client(settings.google_service_account_json)
        return cls(
            client=client,
            spreadsheet_id=settings.google_sheet_id,
            tab=settings.google_sheet_logs_tab,
        )

    def _sheet_range(self, a1: str) -> str:
        return f"{self.tab}!{a1}"

    def read_header(self) -> list[str]:
        values = self.client.get_values(self.spreadsheet_id, self._sheet_range("1:1"))
        return [c.strip() for c in (values[0] if values else [])]

    def ensure_header(self) -> None:
        key = (self.spreadsheet_id, self.tab)
        if key in _header_verified:
            return
        header = self.read_header()
        if header == LOGS_HEADERS:
            _header_verified.add(key)
            return
        if not header:
            logger.warning("Logs sheet header missing; writing default header row")
            self.client.update_values(
                self.spreadsheet_id,
                self._sheet_range("A1"),
                [LOGS_HEADERS],
            )
            _header_verified.add(key)
            return
        raise RuntimeError(
            f"Logs sheet header mismatch. Expected {LOGS_HEADERS} but found {header}. "
            "Update the sheet headers or adjust LOGS_HEADERS in code."
        )

    def append_log_row(self, row: dict[str, Any]) -> None:
        self.ensure_header()
        line = [_safe_str(row.get(h)) for h in LOGS_HEADERS]
        last_col = chr(ord("A") + len(LOGS_HEADERS) - 1)
        self.client.append_values(
            self.spreadsheet_id,
            self._sheet_range(f"A:{last_col}"),
            [line],
        )
        db_row = {h: _safe_str(row.get(h)) for h in LOGS_HEADERS}
        call_logs_insert(db_row)
