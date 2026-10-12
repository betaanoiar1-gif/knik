"""OpenCode adapter.

Tasks go to the real ``opencode serve`` HTTP API pinned in
``packages/protocol/src/groups/session.ts``:

- GET  /api/info
- POST /api/session
- POST /api/session/:id/prompt
- POST /api/session/:id/interrupt
- GET  /api/session/:id/message
- GET  /api/event

Provider configuration uses OpenCode's own provider packages, not Free Claude
Code's responses proxy. The KiosAPI key is passed only as an environment
reference inside this process.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from base64 import b64encode
from pathlib import Path

import httpx

from ..kios import KIOS_ENV_VAR, KiosError, opencode_config, opencode_model_ref, plan_endpoints
from ..process import ManagedProcess
from ..redact import Redactor
from ..secrets import SecretStore
from ..settings import Settings
from .base import EngineError, EngineTask

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENCODE_ROOT = REPO_ROOT / "third_party" / "opencode"
PINNED_COMMIT = "80fb31ebb7e8dfa5c26fc51ca81da8e06b618a52"


def serve_argv(binary: str, port: int) -> list[str]:
    return [binary, "serve", "--hostname", "127.0.0.1", "--port", str(port)]


class OpenCodeEngine:
    id = "opencode"
    display_name = "OpenCode"

    def __init__(
        self,
        settings: Settings,
        secrets: SecretStore,
        data_dir: Path,
        redactor: Redactor,
        *,
        binary: str | None = None,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.data_dir = data_dir
        self.redactor = redactor
        self.binary = binary or os.environ.get("NEXUS_OPENCODE_BIN")
        self.home = data_dir / "engines" / "opencode" / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._process: ManagedProcess | None = None
        self._port: int | None = None
        self._last_error: str | None = None
        self._active: dict[str, str] = {}

    def status(self) -> dict[str, object]:
        binary = self._binary()
        source_ok = (OPENCODE_ROOT / "packages" / "cli" / "src" / "index.ts").is_file()
        commit = _submodule_commit()
        running = bool(self._process and self._process.running())
        return {
            "id": self.id,
            "name": self.display_name,
            "kind": "upstream-process",
            "installed": source_ok or bool(binary),
            "source_commit": commit,
            "source_ok": source_ok and commit == PINNED_COMMIT,
            "runtime_ready": bool(binary),
            "runtime_detail": (
                f"OpenCode binary: {binary}"
                if binary
                else "opencode is not on PATH. Install OpenCode 2 or set NEXUS_OPENCODE_BIN. The source submodule is not a substitute for the binary."
            ),
            "binary": binary,
            "credential_configured": self.secrets.configured("kios_api_key"),
            "credential_verified": False,
            "credential_note": "OpenCode receives the key only through its own provider settings env reference.",
            "running": running,
            "endpoint": f"http://127.0.0.1:{self._port}" if running and self._port else None,
            "pid": self._process.process.pid if running and self._process else None,
            "capabilities": self._capabilities(binary, source_ok),
            "last_error": self._last_error,
            "real_engine": True,
        }

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._process and self._process.running():
                return self.status()
            binary = self._binary()
            if not binary:
                self._last_error = "OpenCode binary is not installed."
                raise EngineError("runtime_missing", self._last_error)
            port = _free_port()
            try:
                env, config_path = self._prepare_env()
            except KiosError as exc:
                self._last_error = exc.message
                raise EngineError(exc.code, exc.message) from exc
            env["HOME"] = str(self.home)
            env["XDG_CONFIG_HOME"] = str(self.home / ".config")
            env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
            argv = serve_argv(binary, port)
            try:
                process = ManagedProcess.spawn(argv, cwd=self.home, env=env, redactor=self.redactor)
            except OSError as exc:
                self._last_error = f"Could not start OpenCode: {exc.__class__.__name__}"
                raise EngineError("start_failed", self._last_error) from exc
            self._process = process
            self._port = port
            password = self.secrets.ensure("opencode_server_password")
            if not _wait_http(
                f"http://127.0.0.1:{port}/api/info",
                timeout=25,
                headers={"authorization": _basic(password)},
            ):
                logs = "\n".join(process.snapshot()[-40:])
                process.stop()
                self._process = None
                self._last_error = self.redactor.redact(
                    "OpenCode serve did not become healthy. " + logs
                )
                raise EngineError("start_failed", self._last_error)
            self._last_error = None
            self._config_path = config_path
            return self.status()

    def stop(self) -> dict[str, object]:
        with self._lock:
            if self._process:
                self._process.stop()
                self._process = None
            self._port = None
            return self.status()

    def run(self, task: EngineTask) -> None:
        if not (self._process and self._process.running()):
            self.start()
        if self._port is None:
            raise EngineError("not_running", "OpenCode is not running.")
        if not self.secrets.configured("kios_api_key"):
            raise EngineError(
                "auth",
                "OpenCode is running, but KiosAPI credentials are not configured. The task was not sent.",
            )
        password = self.secrets.ensure("opencode_server_password")
        headers = {
            "authorization": _basic(password),
            "content-type": "application/json",
            "x-opencode-directory": task.project,
        }
        model = opencode_model_ref(self.settings)
        plan = plan_endpoints(self.settings)
        task.emit(
            "system",
            f"engine=opencode provider={model['providerID']} upstream_model={plan.model}",
        )
        base = f"http://127.0.0.1:{self._port}"
        payload = {
            "title": "NEXUS task",
            "model": model,
            "location": {"directory": task.project},
        }
        try:
            with httpx.Client(timeout=self.settings.task_timeout_seconds) as client:
                created = client.post(f"{base}/api/session", headers=headers, json=payload)
                if created.status_code >= 400:
                    raise EngineError("provider", _error_text(created, self.redactor))
                session_id = _session_id(created.json())
                if not session_id:
                    raise EngineError("protocol", "OpenCode did not return a session id.")
                self._active[task.task_id] = session_id
                task.emit("system", f"opencode_session={session_id}")
                prompt = {
                    "text": task.prompt,
                    "skills": [{"id": skill} for skill in task.skills],
                }
                accepted = client.post(
                    f"{base}/api/session/{session_id}/prompt",
                    headers=headers,
                    json=prompt,
                )
                if accepted.status_code >= 400:
                    raise EngineError("provider", _error_text(accepted, self.redactor))
                task.emit("stdout", self.redactor.redact(accepted.text[:1500]))
                self._follow(client, base, session_id, headers, task)
        except httpx.HTTPError as exc:
            raise EngineError("network", f"OpenCode request failed: {exc.__class__.__name__}") from exc
        finally:
            self._active.pop(task.task_id, None)

    def cancel(self, task: EngineTask) -> None:
        task.cancel_event.set()
        session_id = self._active.get(task.task_id)
        if not session_id or self._port is None:
            return
        password = self.secrets.get("opencode_server_password")
        if not password:
            return
        headers = {"authorization": _basic(password), "x-opencode-directory": task.project}
        try:
            httpx.post(
                f"http://127.0.0.1:{self._port}/api/session/{session_id}/interrupt",
                headers=headers,
                timeout=5,
            )
        except httpx.HTTPError:
            return

    def _follow(
        self,
        client: httpx.Client,
        base: str,
        session_id: str,
        headers: dict[str, str],
        task: EngineTask,
    ) -> None:
        seen = 0
        idle_polls = 0
        while not task.cancelled():
            response = client.get(
                f"{base}/api/session/{session_id}/message",
                headers=headers,
                params={"limit": 50, "order": "asc"},
            )
            if response.status_code >= 400:
                task.emit("stderr", _error_text(response, self.redactor))
                return
            messages = _messages(response.json())
            for message in messages[seen:]:
                text = _message_text(message)
                if text:
                    task.emit("stdout", self.redactor.redact(text))
            seen = len(messages)
            if messages and _looks_idle(messages[-1]):
                idle_polls += 1
                if idle_polls >= 2:
                    return
            else:
                idle_polls = 0
            if task.cancel_event.wait(0.4):
                task.emit("system", "cancellation requested")
                self.cancel(task)
                return

    def _prepare_env(self) -> tuple[dict[str, str], Path]:
        include = self.secrets.configured("kios_api_key")
        file_config, overlay = opencode_config(self.settings, include_provider=include)
        config_dir = self.data_dir / "engines" / "opencode" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "opencode.json"
        config_path.write_text(json.dumps(file_config, indent=2) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"NEXUS_AUTH_TOKEN", "NEXUS_KIOSAPI_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
        }
        env["OPENCODE_CONFIG"] = str(config_path)
        if overlay:
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(overlay, separators=(",", ":"))
        env["OPENCODE_SERVER_PASSWORD"] = self.secrets.ensure("opencode_server_password")
        env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
        key = self.secrets.get("kios_api_key")
        if key:
            env[KIOS_ENV_VAR] = key
        return env, config_path

    def _binary(self) -> str | None:
        if self.binary and (Path(self.binary).is_file() or shutil.which(self.binary)):
            return self.binary if Path(self.binary).is_file() else shutil.which(self.binary)
        return shutil.which("opencode")

    def _capabilities(self, binary: str | None, source_ok: bool) -> list[str]:
        caps = []
        if source_ok:
            caps.extend(
                [
                    "source-submodule",
                    "session-api",
                    "skill-discovery",
                    "anthropic-compatible-provider",
                    "openai-compatible-provider",
                ]
            )
        if binary:
            caps.append("serve")
        return caps


def _submodule_commit() -> str | None:
    git = OPENCODE_ROOT / ".git"
    if not git.exists():
        return None
    head = OPENCODE_ROOT / ".git" / "HEAD"
    # A submodule checkout has a .git file pointing at the parent gitdir.
    try:
        if git.is_file():
            pointer = git.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                gitdir = Path(pointer.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (OPENCODE_ROOT / gitdir).resolve()
                head = gitdir / "HEAD"
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("ref:"):
        return None
    return text or None


def _basic(password: str) -> str:
    token = b64encode(f"opencode:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, timeout: float, headers: dict[str, str]) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, headers=headers, timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.25)
            continue
        if response.status_code < 500:
            return True
        time.sleep(0.25)
    return False


def _session_id(payload: object) -> str | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("id"), str):
            return payload["id"]
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            return data["id"]
    return None


def _messages(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, dict):
        data = payload.get("data", payload.get("messages"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _message_text(message: dict[str, object]) -> str:
    for key in ("text", "content", "summary"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    parts = message.get("parts")
    if isinstance(parts, list):
        chunks = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "".join(chunks)
    return ""


def _looks_idle(message: dict[str, object]) -> bool:
    # Only the upstream idle marker ends a turn. An assistant message can arrive
    # before OpenCode has finished tool calls, so it is not treated as completion.
    return str(message.get("type") or "") == "idle"


def _error_text(response: httpx.Response, redactor: Redactor) -> str:
    return redactor.redact(f"OpenCode HTTP {response.status_code}: {response.text[:800]}")
