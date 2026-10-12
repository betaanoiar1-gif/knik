"""Redact credentials before they reach logs, the UI, or persisted task output."""

from __future__ import annotations

import re
from collections.abc import Iterable

_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/=\-]+)"),
    re.compile(r"(?i)(x-api-key[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]+)"),
    re.compile(r"(?i)(\"api_keys?\"\s*:\s*\")([^\"]*)(\")"),
    re.compile(r"(?i)(\"authToken\"\s*:\s*\")([^\"]*)(\")"),
    re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{6,})"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=\-]{8,})"),
)


class Redactor:
    def __init__(self, secrets: Iterable[str] | None = None) -> None:
        self._secrets = _usable_secrets(secrets or ())

    def extend(self, secrets: Iterable[str]) -> None:
        self._secrets.extend(_usable_secrets(secrets))

    def redact(self, text: str) -> str:
        if not text:
            return text
        redacted = text
        for secret in self._secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED]")
        for pattern in _PATTERNS:
            redacted = pattern.sub(_pattern_replace, redacted)
        return redacted

    def redact_obj(self, value: object) -> object:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_obj(item) for item in value]
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                if _sensitive_key(str(key)):
                    cleaned[key] = "[REDACTED]" if item else item
                else:
                    cleaned[key] = self.redact_obj(item)
            return cleaned
        return value


def _usable_secrets(secrets: Iterable[str]) -> list[str]:
    found: list[str] = []
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 6 and secret not in found:
            found.append(secret)
    found.sort(key=len, reverse=True)
    return found


def _sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return lowered in {
        "api_key",
        "api_keys",
        "apikey",
        "authorization",
        "auth_token",
        "authtoken",
        "password",
        "secret",
        "token",
        "x_api_key",
        "proxy_token",
        "server_password",
    }


def _pattern_replace(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 3:
        return f"{match.group(1)}[REDACTED]{match.group(3)}"
    if match.lastindex and match.lastindex >= 2:
        return f"{match.group(1)}[REDACTED]"
    return "[REDACTED]"
