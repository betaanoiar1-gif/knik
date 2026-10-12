"""Mode-0600 secret store. Values are never returned by the HTTP API."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

SECRET_NAMES = (
    "kios_api_key",
    "auth_token",
    "fcc_proxy_token",
    "opencode_server_password",
)


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})
        else:
            self._enforce_mode()

    def get(self, name: str) -> str | None:
        if name not in SECRET_NAMES:
            raise KeyError(name)
        value = self._read().get(name)
        return value if isinstance(value, str) and value else None

    def set(self, name: str, value: str) -> None:
        if name not in SECRET_NAMES:
            raise KeyError(name)
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("secret is empty")
        if "\n" in cleaned or "\r" in cleaned:
            raise ValueError("secret must be a single line")
        data = self._read()
        data[name] = cleaned
        self._write(data)

    def delete(self, name: str) -> None:
        if name not in SECRET_NAMES:
            raise KeyError(name)
        data = self._read()
        data.pop(name, None)
        self._write(data)

    def configured(self, name: str) -> bool:
        return self.get(name) is not None

    def ensure(self, name: str, nbytes: int = 24) -> str:
        current = self.get(name)
        if current:
            return current
        generated = secrets.token_urlsafe(nbytes)
        self.set(name, generated)
        return generated

    def known_values(self) -> list[str]:
        data = self._read()
        return [value for value in data.values() if isinstance(value, str)]

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, str)}

    def _write(self, data: dict[str, str]) -> None:
        payload = json.dumps(data, indent=2) + "\n"
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".secrets-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self._enforce_mode()

    def _enforce_mode(self) -> None:
        os.chmod(self.path, 0o600)
