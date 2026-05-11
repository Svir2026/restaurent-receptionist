from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Restaurant Inbound API"
    app_version: str = "0.1.0"

    # Runtime mode: controls request validation strictness
    mode: Literal["development", "testing"] = Field(default="development", alias="MODE")

    # Sheets
    google_sheet_id: str = Field(..., alias="GOOGLE_SHEET_ID")
    google_sheet_tab: str = Field(default="Orders", alias="GOOGLE_SHEET_TAB")
    restaurant_timezone: str = Field(..., alias="RESTAURANT_TIMEZONE")
    lookahead_hours: int = Field(default=12, alias="LOOKAHEAD_HOURS")

    # ISO 3166-1 alpha-2 region (e.g. SE) used first when parsing numbers without a leading +.
    # Typical production: SE for Sweden; add PHONE_FALLBACK_REGIONS=PK when testers use Pakistani national format.
    phone_default_region: str | None = Field(default="SE", alias="PHONE_DEFAULT_REGION")

    # Comma-separated extra regions to try if parsing with the default region fails (e.g. "PK" or "PK,SE").
    phone_fallback_regions: str = Field(default="", alias="PHONE_FALLBACK_REGIONS")

    # Service account JSON content or path to a JSON key file.
    google_service_account_json: str = Field(..., alias="GOOGLE_SERVICE_ACCOUNT_JSON")


settings = Settings()
