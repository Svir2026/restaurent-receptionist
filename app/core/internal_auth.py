from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import settings


UNAUTHORIZED_DETAIL = "Unauthorized"


def require_svir_internal_secret(
    x_svir_internal_secret: Annotated[
        str | None,
        Header(alias="X-Svir-Internal-Secret"),
    ] = None,
) -> None:
    """
    Protect internal Railway endpoints.

    The caller must send:
    X-Svir-Internal-Secret: <secret>
    """

    provided_secret = (x_svir_internal_secret or "").strip()
    expected_secret = (
        settings.svir_internal_api_secret.get_secret_value()
    )

    if not provided_secret or not hmac.compare_digest(
        provided_secret,
        expected_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=UNAUTHORIZED_DETAIL,
        )
