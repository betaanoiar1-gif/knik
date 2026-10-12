"""Non-secret settings. Defaults are the requested KiosAPI values and stay editable."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

WireFormat = Literal["anthropic_messages", "openai_chat"]
AuthStyle = Literal["auto", "x-api-key", "bearer"]
MessagingPlatform = Literal["none", "telegram", "discord"]

DEFAULT_KIOS_HOST = "https://mp.kaggle.net"
DEFAULT_KIOS_MODEL = "kiosapi/north-mini-code"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kios_host: str = DEFAULT_KIOS_HOST
    kios_model: str = DEFAULT_KIOS_MODEL
    kios_wire_format: WireFormat = "anthropic_messages"
    kios_auth_style: AuthStyle = "auto"
    kios_anthropic_version: str = "2023-06-01"
    kios_rpm: int = Field(default=30, ge=1, le=600)
    kios_max_retries: int = Field(default=3, ge=0, le=8)
    kios_retry_cap_seconds: float = Field(default=20, gt=0, le=120)
    kios_timeout_seconds: float = Field(default=90, gt=1, le=600)
    kios_context_tokens: int = Field(default=128000, ge=1024, le=2_000_000)
    kios_max_output_tokens: int = Field(default=4096, ge=1, le=128000)
    task_timeout_seconds: int = Field(default=600, ge=5, le=7200)
    max_concurrent_tasks: int = Field(default=2, ge=1, le=8)
    allowed_roots: list[str] = Field(default_factory=list)
    fcc_messaging_platform: MessagingPlatform = "none"
    enable_mock_engine: bool = False

    @field_validator("kios_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        return normalize_host(value)

    @field_validator("kios_model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        model = value.strip()
        if not model or any(char in model for char in "\r\n\t "):
            raise ValueError("model must be a non-empty single token")
        if len(model) > 256:
            raise ValueError("model is too long")
        return model

    @field_validator("kios_anthropic_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        version = value.strip()
        if not version or any(char in version for char in "\r\n "):
            raise ValueError("anthropic version must be a single token")
        return version


def normalize_host(value: str) -> str:
    host = value.strip().rstrip("/")
    parsed = urlsplit(host)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("host must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ValueError("host must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("host must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("settings file is not an object")
    return Settings.model_validate(raw)


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
