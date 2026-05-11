from __future__ import annotations

import hashlib
import hmac
import time


def verify_elevenlabs_signature(*, raw_body: str, sig_header: str | None, secret: str) -> None:
    """Verify ElevenLabs webhook HMAC (same scheme as elevenlabs-js WebhooksClient.constructEvent)."""
    if not secret.strip():
        raise ValueError("Webhook secret is empty")
    if not sig_header:
        raise ValueError("Missing elevenlabs-signature header")

    parts = [p.strip() for p in sig_header.split(",")]
    timestamp: str | None = None
    signature: str | None = None
    for p in parts:
        if p.startswith("t="):
            timestamp = p[2:]
        elif p.startswith("v0="):
            signature = p

    if not timestamp or not signature:
        raise ValueError("No signature hash found with expected scheme v0")

    try:
        req_ts_ms = int(timestamp, 10) * 1000
    except ValueError as e:
        raise ValueError("Invalid timestamp in signature header") from e

    now_ms = time.time() * 1000
    if req_ts_ms < now_ms - 30 * 60 * 1000:
        raise ValueError("Timestamp outside the tolerance zone")

    message = f"{timestamp}.{raw_body}"
    digest = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected = "v0=" + digest.hex()

    if not hmac.compare_digest(signature.encode("ascii"), expected.encode("ascii")):
        raise ValueError("Signature hash does not match the expected signature hash for payload")
