from __future__ import annotations

from pydantic import Field, SecretStr
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

    restaurant_timezone: str = Field(..., alias="RESTAURANT_TIMEZONE")
    lookahead_hours: int = Field(default=12, alias="LOOKAHEAD_HOURS")

    # ElevenLabs post-call webhook HMAC secret (from ElevenLabs dashboard).
    elevenlabs_webhook_secret: str = Field(
        ...,
        alias="ELEVENLABS_WEBHOOK_SECRET",
        min_length=1,
    )

    # Secret for secure communication between Svir Kök and Railway.
    svir_internal_api_secret: SecretStr = Field(
        ...,
        alias="SVIR_INTERNAL_API_SECRET",
        min_length=32,
    )
    # ElevenLabs provisioning
    elevenlabs_api_key: SecretStr = Field(
        ...,
        alias="ELEVENLABS_API_KEY",
        min_length=1,
    )

    elevenlabs_template_agent_id: str = Field(
        ...,
        alias="ELEVENLABS_TEMPLATE_AGENT_ID",
        min_length=1,
    )
    # Supabase (service role: server-side only; bypasses RLS)
    supabase_url: str = Field(..., alias="SUPABASE_URL", min_length=1)
    supabase_service_role_key: str = Field(
        ...,
        alias="SUPABASE_SERVICE_ROLE_KEY",
        min_length=1,
    )
    supabase_orders_table: str = Field(
        default="orders",
        alias="SUPABASE_ORDERS_TABLE",
    )
    supabase_logs_table: str = Field(
        default="call_logs",
        alias="SUPABASE_LOGS_TABLE",
    )


settings = Settings()
