from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)


def _raise_better_http_error(e: HttpError, *, spreadsheet_id: str) -> None:
    status = getattr(getattr(e, "resp", None), "status", None)
    if status == 403:
        raise PermissionError(
            "Google Sheets API returned 403 (permission denied). "
            f"Share the spreadsheet {spreadsheet_id!r} with the service account email "
            "(the 'client_email' field inside your GOOGLE_SERVICE_ACCOUNT_JSON key), "
            "or use credentials that already have access."
        ) from e
    raise


@dataclass(frozen=True)
class SheetsClient:
    service: Any

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[str]]:
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_a1)
                .execute()
            )
            return result.get("values", []) or []
        except HttpError as e:
            _raise_better_http_error(e, spreadsheet_id=spreadsheet_id)

    def append_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
    ) -> dict[str, Any]:
        try:
            return (
                self.service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=range_a1,
                    valueInputOption="RAW",
                    # IMPORTANT: We rely on pre-formatted rows (dropdown validation, etc.).
                    # INSERT_ROWS creates new rows that do not inherit validation.
                    insertDataOption="OVERWRITE",
                    body={"values": values},
                )
                .execute()
            )
        except HttpError as e:
            _raise_better_http_error(e, spreadsheet_id=spreadsheet_id)

    def update_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
    ) -> dict[str, Any]:
        try:
            return (
                self.service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range_a1,
                    valueInputOption="RAW",
                    body={"values": values},
                )
                .execute()
            )
        except HttpError as e:
            _raise_better_http_error(e, spreadsheet_id=spreadsheet_id)


@lru_cache(maxsize=1)
def get_sheets_client(service_account_json: str) -> SheetsClient:
    raw = (service_account_json or "").strip()
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is required")

    # Accept either JSON content or a filesystem path to a JSON key file.
    if raw.startswith("{"):
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError("Invalid service account JSON content") from e
    else:
        path = os.path.expandvars(raw)
        try:
            with open(path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except FileNotFoundError as e:
            raise ValueError(f"Service account key file not found: {path}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in service account key file: {path}") from e

    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    logger.info(
        "Google Sheets client initialized (service_account=%s)",
        (info.get("client_email") or "unknown"),
    )
    return SheetsClient(service=service)
