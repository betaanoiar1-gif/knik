"""Integration tests for the combined NEXUS control plane."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from nexus.app import _authorized, create_app
from nexus.engines.base import EngineTask
from nexus.engines.fcc import ENTRYPOINT, fcc_argv
from nexus.engines.mock import MockEngine
from nexus.engines.opencode import serve_argv
from nexus.kios import (
    FCC_KIOS_PROVIDER_ID,
    OPENCODE_ANTHROPIC_PACKAGE,
    OPENCODE_OPENAI_PACKAGE,
    KiosClient,
    KiosError,
    fcc_env,
    opencode_config,
    plan_endpoints,
    prepare_request,
)
from nexus.redact import Redactor
from nexus.secrets import SecretStore
from nexus.settings import Settings
from nexus.skills_mgr import SkillCatalog, install_skill, uninstall_skill
from nexus.tasks import TaskManager

TOKEN = "nexus-control-token"
SECRET = "nexus-test-secret-value"


class RequestStub:
    def __init__(self, host: str, header: str = "", query: str = "") -> None:
        self.headers = {"authorization": header} if header else {}
        self.query_params = {"token": query} if query else {}
        self.client = type("Client", (), {"host": host})()


@pytest.fixture()
def app(tmp_path: Path):
    application = create_app(
        tmp_path / "data",
        host="127.0.0.1",
        auth_token=TOKEN,
        inject_ui_token=False,
        enable_mock=True,
    )
    return application


@pytest.fixture()
def client(app):
    test_client = TestClient(app)
    test_client.headers["Authorization"] = f"Bearer {TOKEN}"
    return test_client


def test_health_and_ui_are_wired(client: TestClient):
    health = client.get("/api/health")
    assert health.status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert "data-action=\"submit-task\"" in page.text
    assert "data-action=\"cancel-task\"" in page.text
    assert "data-action=\"probe-live\"" in page.text
    assert TOKEN not in page.text
    script = client.get("/static/app.js")
    assert script.status_code == 200
    for action in (
        "submit-task",
        "cancel-task",
        "save-settings",
        "save-secret",
        "probe-dry",
        "probe-live",
        "refresh-diagnostics",
        "validate-project",
        "refresh-engines",
        "refresh-skills",
    ):
        assert action in page.text
        assert f'action === "{action}"' in script.text or f'action === "{action}"' in script.text or action in script.text
    for action in ("start-engine", "stop-engine", "install-skill", "uninstall-skill"):
        assert action in script.text


def test_auth_required_off_loopback(tmp_path: Path):
    app = create_app(tmp_path / "data", host="0.0.0.0", auth_token=TOKEN, enable_mock=True)
    anonymous = TestClient(app)
    assert anonymous.get("/api/health").status_code == 200
    assert anonymous.get("/api/engines").status_code == 401
    assert anonymous.get("/api/engines", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert _authorized(RequestStub("127.0.0.1"), TOKEN, "0.0.0.0") is False
    assert _authorized(RequestStub("127.0.0.1", header=f"Bearer {TOKEN}"), TOKEN, "0.0.0.0") is True


def test_engine_discovery_and_isolation(client: TestClient, app):
    engines = client.get("/api/engines").json()["engines"]
    ids = {item["id"] for item in engines}
    assert {"free-claude-code", "opencode", "mock"} <= ids
    fcc = next(item for item in engines if item["id"] == "free-claude-code")
    opencode = next(item for item in engines if item["id"] == "opencode")
    assert fcc["real_engine"] is True
    assert opencode["real_engine"] is True
    assert fcc["source_ok"] is True
    assert "custom-provider-config" in fcc["capabilities"]
    app.state.engines["mock"].fail_start = True
    failed = client.post("/api/engines/mock/start")
    assert failed.status_code == 409
    again = client.get("/api/engines").json()["engines"]
    assert any(item["id"] == "opencode" and "status_failed" not in item for item in again)
    assert any(item["id"] == "free-claude-code" for item in again)


def test_tasks_route_to_the_selected_engine_and_can_continue(client: TestClient, app):
    project = Path(app.state.settings.allowed_roots[0]) / "demo"
    project.mkdir()
    mock = app.state.engines["mock"]
    first = client.post(
        "/api/tasks",
        json={"engine_id": "mock", "prompt": "first task", "project": str(project), "skills": []},
    )
    assert first.status_code == 201
    done = _wait(client, first.json()["id"])
    assert done["status"] == "succeeded"
    assert done["engine_id"] == "mock"
    assert any("mock completed: first task" in event["text"] for event in done["events"])
    assert mock.calls == ["first task"]

    failed = client.post(
        "/api/tasks",
        json={"engine_id": "mock", "prompt": "FAIL second", "project": str(project), "skills": []},
    )
    failed_done = _wait(client, failed.json()["id"])
    assert failed_done["status"] == "failed"
    assert failed_done["session_id"] != done["session_id"]

    third = client.post(
        "/api/tasks",
        json={"engine_id": "mock", "prompt": "third after failure", "project": str(project), "skills": []},
    )
    third_done = _wait(client, third.json()["id"])
    assert third_done["status"] == "succeeded"
    assert mock.calls == ["first task", "FAIL second", "third after failure"]
    sessions = client.get("/api/sessions").json()["sessions"]
    assert {item["engine_id"] for item in sessions} == {"mock"}


def test_cancel_cleans_up(client: TestClient, app):
    project = Path(app.state.settings.allowed_roots[0]) / "cancel"
    project.mkdir()
    created = client.post(
        "/api/tasks",
        json={"engine_id": "mock", "prompt": "WAIT please", "project": str(project), "skills": []},
    )
    task_id = created.json()["id"]
    time.sleep(0.05)
    cancelled = client.post(f"/api/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    done = _wait(client, task_id)
    assert done["status"] == "cancelled"
    thread = app.state.tasks._threads[task_id]
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_secret_not_leaked_in_task_output(client: TestClient, app):
    project = Path(app.state.settings.allowed_roots[0]) / "secret"
    project.mkdir()
    stored = client.put("/api/kiosapi/secret", json={"api_key": SECRET})
    assert stored.json() == {"configured": True}
    created = client.post(
        "/api/tasks",
        json={
            "engine_id": "mock",
            "prompt": f"echo {SECRET}",
            "project": str(project),
            "skills": [],
        },
    )
    done = _wait(client, created.json()["id"])
    blob = json.dumps(done)
    assert SECRET not in blob
    assert "[REDACTED]" in blob
    listed = client.get("/api/settings").json()
    assert listed["secrets"]["kios_api_key_configured"] is True
    assert SECRET not in json.dumps(listed)


def test_path_and_settings_guards(client: TestClient):
    rejected = client.post("/api/projects/validate", json={"path": "/etc"})
    assert rejected.status_code == 400
    relative = client.post("/api/projects/validate", json={"path": "../etc"})
    assert relative.status_code == 400
    updated = client.put("/api/settings", json={"kios_model": "kiosapi/other-model", "kios_host": "https://example.test"})
    assert updated.status_code == 200
    assert updated.json()["settings"]["kios_model"] == "kiosapi/other-model"
    assert updated.json()["kios"]["model"] == "kiosapi/other-model"
    assert updated.json()["kios"]["request_url"] == "https://example.test/v1/messages"
    bad_host = client.put("/api/settings", json={"kios_host": "https://user:pass@example.test"})
    assert bad_host.status_code == 422


def test_kios_request_shape_and_retries():
    settings = Settings()
    plan = plan_endpoints(settings)
    assert plan.model == "kiosapi/north-mini-code"
    assert plan.request_url == "https://mp.kaggle.net/v1/messages"
    assert plan.fcc_base_url == "https://mp.kaggle.net/v1"
    assert plan.opencode_package == OPENCODE_ANTHROPIC_PACKAGE
    prepared = prepare_request(settings, "ping", SECRET)
    assert prepared.body["model"] == "kiosapi/north-mini-code"
    assert prepared.headers["x-api-key"] == SECRET
    assert "authorization" not in prepared.headers
    redacted = prepared.redacted(Redactor([SECRET]))
    assert SECRET not in json.dumps(redacted)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url == "https://mp.kaggle.net/v1/messages"
        assert json.loads(request.content)["model"] == "kiosapi/north-mini-code"
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text=f"slow {SECRET}")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "pong"}]})

    client = KiosClient(settings, transport=httpx.MockTransport(handler), sleeper=lambda _: None)
    result = client.complete("ping", SECRET)
    assert result["text"] == "pong"
    assert calls["n"] == 2

    auth_calls = {"n": 0}

    def auth_handler(request: httpx.Request) -> httpx.Response:
        auth_calls["n"] += 1
        return httpx.Response(401, text=f"bad {SECRET}")

    auth_client = KiosClient(settings, transport=httpx.MockTransport(auth_handler), sleeper=lambda _: None)
    with pytest.raises(KiosError) as caught:
        auth_client.complete("ping", SECRET)
    assert caught.value.code == "auth"
    assert SECRET not in caught.value.message
    assert auth_calls["n"] == 1

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    down_settings = settings.model_copy(update={"kios_max_retries": 1})
    down_client = KiosClient(down_settings, transport=httpx.MockTransport(down), sleeper=lambda _: None)
    with pytest.raises(KiosError) as network:
        down_client.complete("ping", SECRET)
    assert network.value.code == "network"


def test_engine_adapters_do_not_share_credentials(tmp_path: Path):
    settings = Settings(kios_auth_style="bearer")
    with pytest.raises(KiosError) as incompatible:
        fcc_env(settings, SECRET, proxy_token="proxy-token-value")
    assert incompatible.value.code == "incompatible_auth"

    settings = Settings()
    env = fcc_env(settings, SECRET, proxy_token="proxy-token-value")
    payload = json.loads(env["FCC_CUSTOM_PROVIDERS"])
    assert payload[0]["provider_id"] == FCC_KIOS_PROVIDER_ID
    assert payload[0]["model_ids"] == ["kiosapi/north-mini-code"]
    assert payload[0]["api_format"] == "anthropic_messages"
    assert payload[0]["base_url"] == "https://mp.kaggle.net/v1"
    assert env["MODEL"] == f"{FCC_KIOS_PROVIDER_ID}/kiosapi/north-mini-code"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "proxy-token-value"
    assert env["ANTHROPIC_AUTH_TOKEN"] != SECRET
    assert env["MESSAGING_PLATFORM"] == "none"
    assert fcc_argv("/usr/bin/python3.14") == ["/usr/bin/python3.14", "-c", ENTRYPOINT]
    assert "free_claude_code.cli.entrypoints" in ENTRYPOINT

    file_config, overlay = opencode_config(settings, include_provider=True)
    blob = json.dumps({"file": file_config, "overlay": overlay})
    assert SECRET not in blob
    assert OPENCODE_ANTHROPIC_PACKAGE in blob
    assert "@opencode/ai/providers/openai/responses" not in blob
    assert "{env:NEXUS_KIOSAPI_API_KEY}" in blob
    openai = Settings(kios_wire_format="openai_chat")
    openai_file, _overlay = opencode_config(openai, include_provider=True)
    assert openai_file["providers"]["nexus-kiosapi"]["package"] == OPENCODE_OPENAI_PACKAGE
    assert serve_argv("/usr/local/bin/opencode", 4096) == [
        "/usr/local/bin/opencode",
        "serve",
        "--hostname",
        "127.0.0.1",
        "--port",
        "4096",
    ]
    store = SecretStore(tmp_path / "secrets.json")
    store.set("kios_api_key", SECRET)
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"


def test_skills_install_does_not_change_originals(tmp_path: Path):
    catalog = SkillCatalog(Path("third_party/skills"))
    skill = catalog.get("tdd")
    before = skill.skill_file.read_bytes()
    project = tmp_path / "proj"
    project.mkdir()
    installed = install_skill(
        catalog,
        "tdd",
        project,
        ["free-claude-code", "opencode"],
        allowed_roots=[tmp_path],
        secrets_path=tmp_path / "secrets.json",
    )
    assert installed["original_unchanged"] is True
    assert skill.skill_file.read_bytes() == before
    fcc_link = project / ".claude" / "skills" / "tdd"
    oc_link = project / ".opencode" / "skills" / "tdd"
    assert fcc_link.is_symlink()
    assert oc_link.is_symlink()
    assert (fcc_link / "SKILL.md").read_bytes() == before
    uninstall_skill("tdd", project, allowed_roots=[tmp_path], secrets_path=tmp_path / "secrets.json")
    assert not fcc_link.exists()
    assert skill.skill_file.read_bytes() == before


def test_two_engines_keep_independent_sessions(tmp_path: Path):
    class Other(MockEngine):
        id = "mock-b"
        display_name = "Mock B"

    redactor = Redactor()
    manager = TaskManager(
        {"mock": MockEngine(), "mock-b": Other()},
        redactor,
        tmp_path,
        max_concurrent=2,
        timeout=30,
    )
    project = str(tmp_path)
    first = manager.submit(engine_id="mock", prompt="one", project=project, skills=[])
    failed = manager.submit(engine_id="mock-b", prompt="FAIL boom", project=project, skills=[])
    _wait_record(manager, first["id"])
    _wait_record(manager, failed["id"])
    sessions = manager.list_sessions()
    assert {item["engine_id"] for item in sessions} == {"mock", "mock-b"}
    assert manager.get(first["id"])["status"] == "succeeded"
    assert manager.get(failed["id"])["status"] == "failed"
    again = manager.submit(engine_id="mock", prompt="after", project=project, skills=[])
    assert _wait_record(manager, again["id"])["status"] == "succeeded"


def test_upstream_snapshot_is_present(client: TestClient):
    report = client.get("/api/diagnostics").json()
    upstream = report["upstream"]
    assert upstream["free_claude_code"]["hashes_ok"] is True
    assert upstream["free_claude_code"]["symbols_ok"] is True
    assert upstream["free_claude_code"]["tests_present"] is True
    assert upstream["skills"]["hashes_ok"] is True
    assert upstream["skills"]["skill_count"] == 38
    assert upstream["opencode"]["symbols_ok"] is True
    assert upstream["opencode"]["pinned_commit_present"] is True
    assert report["secret_file_mode"] == "0o600"


def test_fcc_adapter_launches_configured_interpreter(tmp_path: Path):
    fake = tmp_path / "python3.14"
    log = tmp_path / "fcc-request.json"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "args = sys.argv[1:]\n"
        "if args[:1] == ['-c'] and 'version_info' in args[1]:\n"
        "    print('3.14.7')\n"
        "    raise SystemExit(0)\n"
        "if args[:1] == ['-c'] and 'entrypoints' in args[1]:\n"
        "    class Handler(BaseHTTPRequestHandler):\n"
        "        def do_GET(self):\n"
        "            body = b'{\"status\":\"healthy\"}'\n"
        "            self.send_response(200)\n"
        "            self.send_header('content-type', 'application/json')\n"
        "            self.end_headers()\n"
        "            self.wfile.write(body)\n"
        "        def do_POST(self):\n"
        "            length = int(self.headers.get('Content-Length', '0'))\n"
        "            payload = self.rfile.read(length)\n"
        "            open(os.environ['NEXUS_FAKE_LOG'], 'wb').write(payload)\n"
        "            data = b'data: {\"delta\":{\"type\":\"text_delta\",\"text\":\"fcc-proxy-ok\"}}\\n\\n'\n"
        "            self.send_response(200)\n"
        "            self.send_header('content-type', 'text/event-stream')\n"
        "            self.end_headers()\n"
        "            self.wfile.write(data)\n"
        "        def log_message(self, *args):\n"
        "            return\n"
        "    ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), Handler).serve_forever()\n"
        "raise SystemExit('unexpected ' + ' '.join(sys.argv))\n"
    )
    fake.chmod(0o755)
    os_environ = os.environ.get("NEXUS_FAKE_LOG")
    os.environ["NEXUS_FAKE_LOG"] = str(log)
    try:
        from nexus.engines.fcc import FreeClaudeCodeEngine

        secrets = SecretStore(tmp_path / "secrets.json")
        secrets.set("kios_api_key", SECRET)
        engine = FreeClaudeCodeEngine(
            Settings(),
            secrets,
            tmp_path / "runtime",
            Redactor([SECRET]),
            python_bin=str(fake),
        )
        status = engine.start()
        assert status["running"] is True
        assert status["python_version"] == "3.14.7"
        task = EngineTask("t1", "build the feature", str(tmp_path), ["tdd"], threading.Event())
        engine.run(task)
        engine.stop()
    finally:
        if os_environ is None:
            os.environ.pop("NEXUS_FAKE_LOG", None)
        else:
            os.environ["NEXUS_FAKE_LOG"] = os_environ
    sent = json.loads(log.read_text())
    assert sent["model"] == f"{FCC_KIOS_PROVIDER_ID}/kiosapi/north-mini-code"
    assert "tdd" in sent["messages"][0]["content"]
    assert any(update.text == "fcc-proxy-ok" for update in task.updates)


def test_opencode_adapter_speaks_v2_session_api(tmp_path: Path):
    fake = tmp_path / "opencode"
    log = tmp_path / "opencode-requests.log"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def _send(self, code, payload):\n"
        "        raw = json.dumps(payload).encode()\n"
        "        self.send_response(code)\n"
        "        self.send_header('content-type', 'application/json')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(raw)\n"
        "    def do_GET(self):\n"
        "        path = self.path.split('?', 1)[0]\n"
        "        if path.startswith('/api/info'):\n"
        "            assert self.headers.get('Authorization', '').startswith('Basic ')\n"
        "            self._send(200, {'version': '2.0.27', 'pid': os.getpid(), 'urls': [], 'paths': {'tmp': '/tmp'}})\n"
        "            return\n"
        "        if path.endswith('/message'):\n"
        "            self._send(200, {'data': [{'type': 'assistant', 'text': 'opencode-real-api'}, {'type': 'idle'}]})\n"
        "            return\n"
        "        self._send(404, {})\n"
        "    def do_POST(self):\n"
        "        length = int(self.headers.get('Content-Length') or 0)\n"
        "        body = self.rfile.read(length).decode()\n"
        "        with open(os.environ['NEXUS_FAKE_LOG'], 'a', encoding='utf-8') as handle:\n"
        "            handle.write(self.path + '\\n' + body + '\\n')\n"
        "        if self.path == '/api/session':\n"
        "            self._send(201, {'data': {'id': 'ses_test'}})\n"
        "            return\n"
        "        if self.path.endswith('/prompt'):\n"
        "            self._send(200, {'data': {'id': 'inbox'}})\n"
        "            return\n"
        "        if self.path.endswith('/interrupt'):\n"
        "            self._send(200, {'interrupted': True})\n"
        "            return\n"
        "        self._send(404, {})\n"
        "    def log_message(self, *args):\n"
        "        return\n"
        "ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()\n"
    )
    fake.chmod(0o755)
    previous = os.environ.get("NEXUS_FAKE_LOG")
    os.environ["NEXUS_FAKE_LOG"] = str(log)
    try:
        from nexus.engines.opencode import OpenCodeEngine

        secrets = SecretStore(tmp_path / "secrets.json")
        secrets.set("kios_api_key", SECRET)
        engine = OpenCodeEngine(
            Settings(),
            secrets,
            tmp_path / "runtime",
            Redactor([SECRET]),
            binary=str(fake),
        )
        started = engine.start()
        assert started["running"] is True
        config = (tmp_path / "runtime" / "engines" / "opencode" / "config" / "opencode.json").read_text()
        assert SECRET not in config
        assert OPENCODE_ANTHROPIC_PACKAGE in config
        task = EngineTask("t2", "implement the parser", str(tmp_path), ["tdd"], threading.Event())
        engine.run(task)
        engine.stop()
    finally:
        if previous is None:
            os.environ.pop("NEXUS_FAKE_LOG", None)
        else:
            os.environ["NEXUS_FAKE_LOG"] = previous
    recorded = log.read_text()
    assert "/api/session\n" in recorded
    assert "implement the parser" in recorded
    assert "kiosapi/north-mini-code" in recorded
    assert SECRET not in recorded
    assert any(update.text == "opencode-real-api" for update in task.updates)


def test_direct_client_does_not_replace_model():
    settings = Settings(kios_model="kiosapi/north-mini-code", kios_host="https://mp.kaggle.net/v1")
    plan = plan_endpoints(settings)
    assert plan.request_url == "https://mp.kaggle.net/v1/messages"
    assert plan.model == "kiosapi/north-mini-code"


def _wait(client: TestClient, task_id: str) -> dict:
    for _ in range(100):
        payload = client.get(f"/api/tasks/{task_id}").json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def _wait_record(manager: TaskManager, task_id: str) -> dict:
    for _ in range(100):
        payload = manager.get(task_id)
        if payload["status"] not in {"queued", "running"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("task did not finish")
