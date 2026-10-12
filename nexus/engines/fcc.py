"""Free Claude Code adapter.

This does not reimplement the proxy. It launches the vendored
``free_claude_code.cli.entrypoints:serve`` entrypoint and sends tasks through
that process's ``/v1/messages`` route — the original Anthropic-compatible
ingress. Harness launch remains a separate capability and is reported only
when the corresponding binary exists.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

import httpx

from ..kios import FCC_KIOS_PROVIDER_ID, KiosError, fcc_env, plan_endpoints
from ..process import ManagedProcess
from ..redact import Redactor
from ..secrets import SecretStore
from ..settings import Settings
from .base import EngineError, EngineTask

REPO_ROOT = Path(__file__).resolve().parents[2]
FCC_ROOT = REPO_ROOT / "third_party" / "free-claude-code"
FCC_SRC = FCC_ROOT / "src"
ENTRYPOINT = "from free_claude_code.cli.entrypoints import serve; serve()"


class FreeClaudeCodeEngine:
    id = "free-claude-code"
    display_name = "Free Claude Code"

    def __init__(
        self,
        settings: Settings,
        secrets: SecretStore,
        data_dir: Path,
        redactor: Redactor,
        *,
        python_bin: str | None = None,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.data_dir = data_dir
        self.redactor = redactor
        self.python_bin = python_bin or os.environ.get("NEXUS_FCC_PYTHON")
        self._lock = threading.Lock()
        self._process: ManagedProcess | None = None
        self._port: int | None = None
        self._last_error: str | None = None
        self.home = data_dir / "engines" / "fcc" / "home"
        self.home.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, object]:
        source_ok = (FCC_SRC / "free_claude_code" / "cli" / "entrypoints.py").is_file()
        python = self._python()
        version = _python_version(python) if python else None
        exact = version == "3.14.7" if version else False
        running = bool(self._process and self._process.running())
        detail = self._runtime_detail(python, version, exact, source_ok)
        return {
            "id": self.id,
            "name": self.display_name,
            "kind": "upstream-process",
            "installed": source_ok,
            "source_commit": "4e99ddaa5cf453d4ef22fae7c17f7f3aacc3c52b",
            "source_ok": source_ok,
            "runtime_ready": bool(python and exact and source_ok),
            "runtime_detail": detail,
            "python": python,
            "python_version": version,
            "credential_configured": self.secrets.configured("kios_api_key"),
            "credential_verified": False,
            "credential_note": "A configured key is not a successful live KiosAPI call.",
            "running": running,
            "endpoint": f"http://127.0.0.1:{self._port}" if running and self._port else None,
            "pid": self._process.process.pid if running and self._process else None,
            "capabilities": self._capabilities(python, exact, source_ok),
            "last_error": self._last_error,
            "real_engine": True,
            "preserved_entrypoints": [
                "fcc-server",
                "fcc-claude",
                "fcc-opencode",
                "fcc-codex",
                "fcc-doctor",
            ],
        }

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._process and self._process.running():
                return self.status()
            python = self._python()
            version = _python_version(python) if python else None
            if not (FCC_SRC / "free_claude_code" / "cli" / "entrypoints.py").is_file():
                self._last_error = "Vendored Free Claude Code source is missing."
                raise EngineError("not_installed", self._last_error)
            if not python or version != "3.14.7":
                self._last_error = (
                    "Free Claude Code requires Python 3.14.7 exactly "
                    f"(found {version or 'none'}). The engine was not replaced with a stub."
                )
                raise EngineError("runtime_missing", self._last_error)
            try:
                env = self._child_env()
            except KiosError as exc:
                self._last_error = exc.message
                raise EngineError(exc.code, exc.message) from exc
            port = _free_port()
            env["HOST"] = "127.0.0.1"
            env["PORT"] = str(port)
            env["PYTHONPATH"] = str(FCC_SRC)
            env["HOME"] = str(self.home)
            env["PYTHONNOUSERSITE"] = "1"
            # Do not pass the control-plane token or the OpenCode password into FCC.
            env.pop("NEXUS_AUTH_TOKEN", None)
            env.pop("OPENCODE_SERVER_PASSWORD", None)
            try:
                process = ManagedProcess.spawn(
                    fcc_argv(python),
                    cwd=self.home,
                    env=env,
                    redactor=self.redactor,
                )
            except OSError as exc:
                self._last_error = f"Could not start Free Claude Code: {exc.__class__.__name__}"
                raise EngineError("start_failed", self._last_error) from exc
            self._process = process
            self._port = port
            if not _wait_http(f"http://127.0.0.1:{port}/health", timeout=20):
                logs = "\n".join(process.snapshot()[-30:])
                process.stop()
                self._process = None
                self._last_error = self.redactor.redact(
                    "Free Claude Code did not become healthy. " + logs
                )
                raise EngineError("start_failed", self._last_error)
            self._last_error = None
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
        if self._port is None or self._process is None:
            raise EngineError("not_running", "Free Claude Code is not running.")
        plan = plan_endpoints(self.settings)
        model_ref = f"{FCC_KIOS_PROVIDER_ID}/{plan.model}"
        body = {
            "model": model_ref,
            "max_tokens": self.settings.kios_max_output_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": task.prompt}],
        }
        if task.skills:
            skill_note = (
                "\n\nNEXUS enabled these project skills. Read the matching SKILL.md "
                "before acting: " + ", ".join(task.skills)
            )
            body["messages"] = [{"role": "user", "content": task.prompt + skill_note}]
        token = self.secrets.ensure("fcc_proxy_token")
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "anthropic-version": self.settings.kios_anthropic_version,
        }
        task.emit("system", f"engine=free-claude-code model_ref={model_ref} upstream_model={plan.model}")
        url = f"http://127.0.0.1:{self._port}/v1/messages"
        try:
            with httpx.Client(timeout=self.settings.task_timeout_seconds) as client:
                with client.stream("POST", url, headers=headers, json=body) as response:
                    if response.status_code >= 400:
                        raw = self.redactor.redact(response.read().decode("utf-8", "replace")[:800])
                        raise EngineError(
                            "provider" if response.status_code != 401 else "auth",
                            f"Free Claude Code returned HTTP {response.status_code}. {raw}",
                        )
                    for line in response.iter_lines():
                        if task.cancelled():
                            task.emit("system", "cancellation requested")
                            return
                        if not line or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        text = _sse_text(raw)
                        if text:
                            task.emit("stdout", text)
        except httpx.HTTPError as exc:
            raise EngineError("network", f"Free Claude Code request failed: {exc.__class__.__name__}") from exc

    def cancel(self, task: EngineTask) -> None:
        task.cancel_event.set()

    def _child_env(self) -> dict[str, str]:
        base = {
            key: value
            for key, value in os.environ.items()
            if key not in {"NEXUS_KIOSAPI_API_KEY", "NEXUS_AUTH_TOKEN"}
        }
        key = self.secrets.get("kios_api_key")
        proxy = self.secrets.ensure("fcc_proxy_token")
        base.update(fcc_env(self.settings, key, proxy_token=proxy))
        return base

    def _python(self) -> str | None:
        if self.python_bin and Path(self.python_bin).is_file():
            return self.python_bin
        for name in ("python3.14", "python3"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def _runtime_detail(self, python: str | None, version: str | None, exact: bool, source_ok: bool) -> str:
        if not source_ok:
            return "Upstream source snapshot is missing."
        if not python:
            return "No Python interpreter found. Set NEXUS_FCC_PYTHON to Python 3.14.7."
        if not exact:
            return (
                f"Interpreter {python} is Python {version}, not the upstream pin 3.14.7. "
                "NEXUS will not start FCC on an incompatible interpreter."
            )
        return "Ready to launch the unmodified fcc-server entrypoint."

    def _capabilities(self, python: str | None, exact: bool, source_ok: bool) -> list[str]:
        caps = ["source-snapshot", "custom-provider-config", "anthropic-messages-proxy"]
        if source_ok and python and exact:
            caps.append("serve")
        if shutil.which("claude"):
            caps.append("fcc-claude-harness")
        if shutil.which("opencode"):
            caps.append("fcc-opencode-harness")
        return caps


_VERSION_CACHE: dict[str, str | None] = {}


def fcc_argv(python: str) -> list[str]:
    return [python, "-c", ENTRYPOINT]


def _python_version(python: str) -> str | None:
    if python in _VERSION_CACHE:
        return _VERSION_CACHE[python]
    import subprocess

    try:
        completed = subprocess.run(
            [python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    version = completed.stdout.strip() if completed.returncode == 0 else None
    _VERSION_CACHE[python] = version or None
    return version or None


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, timeout: float) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if response.status_code < 500:
            return True
        time.sleep(0.2)
    return False


def _sse_text(raw: str) -> str:
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(event, dict):
        return ""
    delta = event.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("text"), str):
        return delta["text"]
    return ""
