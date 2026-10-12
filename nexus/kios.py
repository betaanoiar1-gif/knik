"""KiosAPI request shaping, bounded retries, and engine-specific adapters.

The host and model defaults are the values requested for this integration.
They are settings, not constants buried in call sites. The model string is
forwarded exactly; NEXUS does not substitute a guessed model id.

Free Claude Code and OpenCode do not speak to a provider the same way:

- FCC custom providers post Anthropic traffic to ``{base_url}/messages`` with
  ``x-api-key`` (see ``providers/anthropic_messages/transport.py``) or OpenAI
  Chat traffic through the OpenAI SDK at ``base_url``.
- OpenCode 2 selects a provider package. Anthropic-compatible traffic uses
  ``@opencode/ai/providers/anthropic-compatible`` (base URL includes ``/v1``,
  path ``/messages``, ``x-api-key`` or ``authToken``). OpenAI-compatible
  traffic uses ``@opencode/ai/providers/openai-compatible`` (path
  ``/chat/completions``, bearer auth). That is not the FCC launcher package
  ``@opencode/ai/providers/openai/responses``.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .redact import Redactor
from .settings import AuthStyle, Settings, WireFormat

FCC_KIOS_PROVIDER_ID = "custom_" + hashlib.sha256(b"nexus:kiosapi").hexdigest()[:32]
OPENCODE_KIOS_PROVIDER_ID = "nexus-kiosapi"
OPENCODE_ANTHROPIC_PACKAGE = "@opencode/ai/providers/anthropic-compatible"
OPENCODE_OPENAI_PACKAGE = "@opencode/ai/providers/openai-compatible"
KIOS_ENV_VAR = "NEXUS_KIOSAPI_API_KEY"


class KiosError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def public(self) -> dict[str, object]:
        body: dict[str, object] = {"code": self.code, "message": self.message}
        if self.status is not None:
            body["http_status"] = self.status
        return body


@dataclass(frozen=True)
class EndpointPlan:
    wire_format: WireFormat
    request_url: str
    fcc_base_url: str
    opencode_base_url: str
    opencode_package: str
    auth_style: str
    model: str

    def public(self) -> dict[str, str]:
        return {
            "wire_format": self.wire_format,
            "request_url": self.request_url,
            "fcc_base_url": self.fcc_base_url,
            "opencode_base_url": self.opencode_base_url,
            "opencode_package": self.opencode_package,
            "auth_style": self.auth_style,
            "model": self.model,
        }


@dataclass(frozen=True)
class PreparedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: dict[str, object]

    def redacted(self, redactor: Redactor) -> dict[str, object]:
        return {
            "method": self.method,
            "url": self.url,
            "headers": redactor.redact_obj(self.headers),
            "body": redactor.redact_obj(self.body),
        }


def resolve_auth_style(wire_format: WireFormat, requested: AuthStyle) -> str:
    if requested != "auto":
        return requested
    return "x-api-key" if wire_format == "anthropic_messages" else "bearer"


def plan_endpoints(settings: Settings) -> EndpointPlan:
    host = settings.kios_host
    path = urlsplit(host).path
    wire = settings.kios_wire_format
    if wire == "anthropic_messages":
        request_url, api_root = _anthropic_urls(host, path)
        package = OPENCODE_ANTHROPIC_PACKAGE
    else:
        request_url, api_root = _openai_urls(host, path)
        package = OPENCODE_OPENAI_PACKAGE
    return EndpointPlan(
        wire_format=wire,
        request_url=request_url,
        fcc_base_url=api_root,
        opencode_base_url=api_root,
        opencode_package=package,
        auth_style=resolve_auth_style(wire, settings.kios_auth_style),
        model=settings.kios_model,
    )


def _anthropic_urls(host: str, path: str) -> tuple[str, str]:
    if path.endswith("/v1/messages"):
        return host, host[: -len("/messages")]
    if path.endswith("/messages"):
        return host, host[: -len("/messages")]
    if path.endswith("/v1"):
        return f"{host}/messages", host
    return f"{host}/v1/messages", f"{host}/v1"


def _openai_urls(host: str, path: str) -> tuple[str, str]:
    if path.endswith("/chat/completions"):
        root = host[: -len("/chat/completions")]
        return host, root
    if path.endswith("/v1"):
        return f"{host}/chat/completions", host
    return f"{host}/v1/chat/completions", f"{host}/v1"


def build_headers(plan: EndpointPlan, api_key: str | None, *, anthropic_version: str) -> dict[str, str]:
    headers = {"content-type": "application/json", "accept": "application/json"}
    if plan.wire_format == "anthropic_messages":
        headers["anthropic-version"] = anthropic_version
    if not api_key:
        return headers
    if plan.auth_style == "bearer":
        headers["authorization"] = f"Bearer {api_key}"
    else:
        headers["x-api-key"] = api_key
    return headers


def build_body(plan: EndpointPlan, prompt: str, *, max_tokens: int, stream: bool) -> dict[str, object]:
    if plan.wire_format == "anthropic_messages":
        body: dict[str, object] = {
            "model": plan.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if stream:
            body["stream"] = True
        return body
    body = {
        "model": plan.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if stream:
        body["stream"] = True
    return body


def prepare_request(
    settings: Settings,
    prompt: str,
    api_key: str | None,
    *,
    max_tokens: int | None = None,
    stream: bool = False,
) -> PreparedRequest:
    plan = plan_endpoints(settings)
    return PreparedRequest(
        method="POST",
        url=plan.request_url,
        headers=build_headers(
            plan, api_key, anthropic_version=settings.kios_anthropic_version
        ),
        body=build_body(
            plan,
            prompt,
            max_tokens=max_tokens or settings.kios_max_output_tokens,
            stream=stream,
        ),
    )


def fcc_env(settings: Settings, api_key: str | None, *, proxy_token: str) -> dict[str, str]:
    """Environment consumed by the unmodified Free Claude Code process."""

    plan = plan_endpoints(settings)
    if plan.wire_format == "anthropic_messages" and plan.auth_style == "bearer":
        raise KiosError(
            "incompatible_auth",
            "Free Claude Code's anthropic_messages transport sends x-api-key only. "
            "Use auth style x-api-key, or switch the wire format to openai_chat "
            "so FCC uses bearer authentication.",
        )
    provider = {
        "provider_id": FCC_KIOS_PROVIDER_ID,
        "display_name": "KiosAPI",
        "base_url": plan.fcc_base_url,
        "api_keys": [api_key] if api_key else [],
        "api_format": plan.wire_format,
        "reasoning_format": "provider_default",
        "reasoning_history_format": "disabled",
        "model_ids": [plan.model],
    }
    model_ref = f"{FCC_KIOS_PROVIDER_ID}/{plan.model}"
    return {
        "FCC_CUSTOM_PROVIDERS": json.dumps([provider], separators=(",", ":")),
        "MODEL": model_ref,
        "MESSAGING_PLATFORM": settings.fcc_messaging_platform,
        "PROXY_AUTH_ENABLED": "true",
        "ANTHROPIC_AUTH_TOKEN": proxy_token,
        "FCC_OPEN_BROWSER": "false",
        "LOG_RAW_API_PAYLOADS": "false",
        "LOG_RAW_SSE_EVENTS": "false",
        "LOG_RAW_CLI_DIAGNOSTICS": "false",
        "LOG_API_ERROR_TRACEBACKS": "false",
    }


def opencode_config(settings: Settings, *, include_provider: bool) -> tuple[dict[str, object], dict[str, object]]:
    """Return (file, overlay) for OpenCode 2. The API key is an env reference, never a literal."""

    plan = plan_endpoints(settings)
    if not include_provider:
        return {"$schema": "https://opencode.ai/config.json"}, {}
    settings_block: dict[str, str] = {"baseURL": plan.opencode_base_url}
    if plan.auth_style == "bearer":
        settings_block["authToken"] = "{env:" + KIOS_ENV_VAR + "}"
    else:
        settings_block["apiKey"] = "{env:" + KIOS_ENV_VAR + "}"
    model_key = plan.model
    file = {
        "$schema": "https://opencode.ai/config.json",
        "providers": {
            OPENCODE_KIOS_PROVIDER_ID: {
                "name": "KiosAPI",
                "package": plan.opencode_package,
                "settings": settings_block,
                "models": {
                    model_key: {
                        "name": model_key,
                        "capabilities": {
                            "tools": True,
                            "input": ["text"],
                            "output": ["text"],
                        },
                        "limit": {
                            "context": settings.kios_context_tokens,
                            "output": settings.kios_max_output_tokens,
                        },
                    }
                },
            }
        },
    }
    overlay = {
        "model": f"{OPENCODE_KIOS_PROVIDER_ID}/{model_key}",
        "experimental": {
            "policies": [
                {"action": "provider.use", "resource": "*", "effect": "deny"},
                {
                    "action": "provider.use",
                    "resource": OPENCODE_KIOS_PROVIDER_ID,
                    "effect": "allow",
                },
            ]
        },
    }
    return file, overlay


def opencode_model_ref(settings: Settings) -> dict[str, str]:
    plan = plan_endpoints(settings)
    return {"providerID": OPENCODE_KIOS_PROVIDER_ID, "id": plan.model}


Sleep = Callable[[float], None]


class KiosClient:
    """Direct client used for probes and request-shape checks. Engines keep their own transports."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Sleep = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._sleeper = sleeper
        self._clock = clock
        self._window: list[float] = []

    def complete(self, prompt: str, api_key: str, *, stream: bool = False) -> dict[str, object]:
        if not api_key:
            raise KiosError("auth", "KiosAPI credential is not configured.")
        self._redactor = Redactor([api_key])
        prepared = prepare_request(self.settings, prompt, api_key, stream=stream)
        attempts = self.settings.kios_max_retries + 1
        last_error: KiosError | None = None
        with httpx.Client(transport=self._transport, timeout=self.settings.kios_timeout_seconds, follow_redirects=False) as client:
            for attempt in range(1, attempts + 1):
                self._acquire_rate_limit()
                try:
                    response = client.post(prepared.url, headers=prepared.headers, json=prepared.body)
                except httpx.TimeoutException as exc:
                    last_error = KiosError("network", f"KiosAPI timed out: {exc.__class__.__name__}")
                except httpx.TransportError as exc:
                    last_error = KiosError("network", f"KiosAPI network error: {exc.__class__.__name__}")
                else:
                    if response.status_code == 401 or response.status_code == 403:
                        raise self._http_error(response, "auth")
                    if response.status_code == 429:
                        last_error = self._http_error(response, "rate_limit")
                        if attempt < attempts:
                            self._sleeper(self._retry_delay(response, attempt))
                            continue
                        raise last_error
                    if response.status_code >= 500:
                        last_error = self._http_error(response, "provider")
                        if attempt < attempts:
                            self._sleeper(self._retry_delay(response, attempt))
                            continue
                        raise last_error
                    if response.status_code >= 400:
                        raise self._http_error(response, "provider")
                    return {
                        "status": response.status_code,
                        "text": extract_text(response.text, self.settings.kios_wire_format),
                        "raw_excerpt": response.text[:800],
                    }
                if attempt < attempts and last_error is not None and last_error.code == "network":
                    self._sleeper(self._retry_delay(None, attempt))
                    continue
                if last_error is not None:
                    raise last_error
        raise KiosError("provider", "KiosAPI request failed without a response.")

    def _acquire_rate_limit(self) -> None:
        window = 60.0
        limit = self.settings.kios_rpm
        while True:
            now = self._clock()
            self._window = [stamp for stamp in self._window if now - stamp < window]
            if len(self._window) < limit:
                self._window.append(now)
                return
            wait = window - (now - self._window[0])
            self._sleeper(max(0.0, wait))

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            parsed = _retry_after(response)
            if parsed is not None:
                return min(self.settings.kios_retry_cap_seconds, max(0.0, parsed))
        base = min(self.settings.kios_retry_cap_seconds, 0.5 * (2 ** (attempt - 1)))
        return base + random.uniform(0, min(0.25, base))

    def _http_error(self, response: httpx.Response, code: str) -> KiosError:
        redactor = getattr(self, "_redactor", Redactor())
        snippet = redactor.redact(response.text[:500]).strip()
        label = {
            "auth": "KiosAPI rejected authentication",
            "rate_limit": "KiosAPI rate limit",
            "provider": "KiosAPI provider error",
        }[code]
        message = f"{label} (HTTP {response.status_code})."
        if snippet:
            message = f"{message} {snippet}"
        return KiosError(code, message, status=response.status_code)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        return max(0.0, moment.timestamp() - time.time())


def extract_text(payload: str, wire_format: WireFormat) -> str:
    payload = payload.strip()
    if not payload:
        return ""
    if payload.startswith("data:") or "\ndata:" in payload:
        return _extract_sse(payload, wire_format)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload[:2000]
    if not isinstance(data, dict):
        return payload[:2000]
    if wire_format == "anthropic_messages":
        content = data.get("content")
        if isinstance(content, list):
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            return "".join(parts)
        if isinstance(data.get("completion"), str):
            return str(data["completion"])
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message["content"])
    return json.dumps(data)[:2000]


def _extract_sse(payload: str, wire_format: WireFormat) -> str:
    chunks: list[str] = []
    for line in payload.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        delta = event.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            chunks.append(delta["text"])
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            chunks.append(delta["content"])
        choices = event.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            piece = choices[0].get("delta")
            if isinstance(piece, dict) and isinstance(piece.get("content"), str):
                chunks.append(piece["content"])
        if wire_format == "anthropic_messages" and isinstance(event.get("content_block"), dict):
            text = event["content_block"].get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)
