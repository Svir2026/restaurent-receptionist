from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.services.logs_repo import LogsRepository
from app.utils.elevenlabs_log_payload import build_log_row
from app.utils.elevenlabs_signature import verify_elevenlabs_signature

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/elevenlabs/post-call")
async def elevenlabs_post_call_webhook(request: Request) -> dict[str, str]:
    raw_bytes = await request.body()
    try:
        raw_body = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="Request body must be valid UTF-8") from e

    sig_header = request.headers.get("elevenlabs-signature") or request.headers.get("ElevenLabs-Signature")
    secret = (settings.elevenlabs_webhook_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="ELEVENLABS_WEBHOOK_SECRET is not configured")

    try:
        verify_elevenlabs_signature(raw_body=raw_body, sig_header=sig_header, secret=secret)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    try:
        body = json.loads(raw_body) if raw_body.strip() else {}
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from e

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")

    row = build_log_row(body)
    repo = LogsRepository.from_settings()
    try:
        repo.append_log_row(row)
    except RuntimeError as e:
        logger.exception("Logs sheet header or configuration error")
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to append log row to Google Sheets")
        raise HTTPException(status_code=502, detail="Failed to write to Google Sheets") from e

    return {"ok": "true"}
