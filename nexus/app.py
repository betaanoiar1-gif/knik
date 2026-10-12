"""Unified NEXUS HTTP control plane."""

from __future__ import annotations

import hmac
import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .diagnostics import doctor
from .engines.fcc import FreeClaudeCodeEngine
from .engines.mock import MockEngine
from .engines.opencode import OpenCodeEngine
from .kios import KiosClient, KiosError, plan_endpoints, prepare_request
from .paths import PathRejected, resolve_project
from .redact import Redactor
from .secrets import SecretStore
from .settings import Settings, load_settings, save_settings
from .skills_mgr import SkillCatalog, install_skill, installed_map, uninstall_skill
from .tasks import TaskManager

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SKILLS_ROOT = REPO_ROOT / "third_party" / "skills"


class SettingsUpdate(BaseModel):
    kios_host: str | None = None
    kios_model: str | None = None
    kios_wire_format: str | None = None
    kios_auth_style: str | None = None
    kios_anthropic_version: str | None = None
    kios_rpm: int | None = None
    kios_max_retries: int | None = None
    kios_retry_cap_seconds: float | None = None
    kios_timeout_seconds: float | None = None
    kios_context_tokens: int | None = None
    kios_max_output_tokens: int | None = None
    task_timeout_seconds: int | None = None
    max_concurrent_tasks: int | None = None
    allowed_roots: list[str] | None = None
    fcc_messaging_platform: str | None = None
    enable_mock_engine: bool | None = None


class SecretUpdate(BaseModel):
    api_key: str = Field(min_length=1)


class ProjectBody(BaseModel):
    path: str


class SkillInstall(BaseModel):
    name: str
    project: str
    engines: list[str]


class TaskBody(BaseModel):
    engine_id: str
    prompt: str
    project: str
    skills: list[str] = Field(default_factory=list)


class ProbeBody(BaseModel):
    dry_run: bool = True
    prompt: str = "NEXUS connectivity probe. Reply with the single word pong."


def create_app(
    data_dir: Path | None = None,
    *,
    host: str = "127.0.0.1",
    auth_token: str | None = None,
    inject_ui_token: bool = False,
    enable_mock: bool | None = None,
) -> FastAPI:
    data = Path(data_dir or os.environ.get("NEXUS_DATA_DIR") or (Path.home() / ".local" / "share" / "nexus")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    (data / "workspaces").mkdir(exist_ok=True)
    settings_path = data / "settings.json"
    settings = load_settings(settings_path)
    if enable_mock is not None:
        settings.enable_mock_engine = enable_mock
    if not settings.allowed_roots:
        settings.allowed_roots = [str(data / "workspaces")]
    secrets = SecretStore(data / "secrets.json")
    if auth_token:
        secrets.set("auth_token", auth_token)
    else:
        env_token = os.environ.get("NEXUS_AUTH_TOKEN")
        if env_token:
            secrets.set("auth_token", env_token)
        else:
            secrets.ensure("auth_token")
    env_key = os.environ.get("NEXUS_KIOSAPI_API_KEY")
    if env_key and not secrets.configured("kios_api_key"):
        secrets.set("kios_api_key", env_key)
    redactor = Redactor(secrets.known_values())
    catalog = SkillCatalog(SKILLS_ROOT)
    engines: dict[str, object] = {
        "free-claude-code": FreeClaudeCodeEngine(settings, secrets, data, redactor),
        "opencode": OpenCodeEngine(settings, secrets, data, redactor),
    }
    if settings.enable_mock_engine:
        engines["mock"] = MockEngine()
    manager = TaskManager(
        engines,  # type: ignore[arg-type]
        redactor,
        data,
        max_concurrent=settings.max_concurrent_tasks,
        timeout=settings.task_timeout_seconds,
    )
    app = FastAPI(title="NEXUS", version=__version__)
    app.state.settings = settings
    app.state.settings_path = settings_path
    app.state.secrets = secrets
    app.state.redactor = redactor
    app.state.catalog = catalog
    app.state.engines = engines
    app.state.tasks = manager
    app.state.data_dir = data
    app.state.bind_host = host
    app.state.inject_ui_token = inject_ui_token
    app.state.lock = threading.RLock()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if _open_path(request.url.path):
            return await call_next(request)
        if not _authorized(request, secrets.get("auth_token") or "", host):
            if request.url.path == "/":
                return await call_next(request)
            return JSONResponse({"error": {"code": "unauthorized", "message": "NEXUS control token required."}}, status_code=401)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        token = secrets.get("auth_token") or ""
        injected = token if inject_ui_token else ""
        html = html.replace("{{UI_TOKEN}}", _html_escape(injected))
        html = html.replace("{{INJECTED}}", "1" if inject_ui_token else "0")
        html = html.replace("{{VERSION}}", __version__)
        return HTMLResponse(html)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/engines")
    def list_engines() -> dict[str, object]:
        items = []
        for engine in engines.values():
            try:
                items.append(engine.status())
            except Exception as exc:
                items.append(
                    {
                        "id": getattr(engine, "id", "unknown"),
                        "running": False,
                        "last_error": redactor.redact(str(exc)),
                        "status_failed": True,
                        "real_engine": getattr(engine, "id", "") != "mock",
                    }
                )
        return {"engines": items}

    @app.post("/api/engines/{engine_id}/start")
    def start_engine(engine_id: str) -> dict[str, object]:
        engine = _engine(engines, engine_id)
        try:
            return {"engine": engine.start()}
        except Exception as exc:
            _isolate(engine_id, exc, redactor)
            raise HTTPException(status_code=409, detail=_error_payload(exc, redactor)) from exc

    @app.post("/api/engines/{engine_id}/stop")
    def stop_engine(engine_id: str) -> dict[str, object]:
        engine = _engine(engines, engine_id)
        try:
            return {"engine": engine.stop()}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=_error_payload(exc, redactor)) from exc

    @app.get("/api/settings")
    def get_settings() -> dict[str, object]:
        current: Settings = app.state.settings
        return {
            "settings": current.model_dump(mode="json"),
            "kios": plan_endpoints(current).public(),
            "secrets": {
                "kios_api_key_configured": secrets.configured("kios_api_key"),
                "auth_token_configured": secrets.configured("auth_token"),
            },
        }

    @app.put("/api/settings")
    def put_settings(update: SettingsUpdate) -> dict[str, object]:
        with app.state.lock:
            current: Settings = app.state.settings
            data = current.model_dump()
            for key, value in update.model_dump(exclude_unset=True).items():
                data[key] = value
            try:
                refreshed = Settings.model_validate(data)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if refreshed.allowed_roots:
                _validate_roots(refreshed.allowed_roots, secrets.path)
            save_settings(settings_path, refreshed)
            app.state.settings = refreshed
            for engine in engines.values():
                if hasattr(engine, "settings"):
                    engine.settings = refreshed
            manager.max_concurrent = refreshed.max_concurrent_tasks
            manager.timeout = refreshed.task_timeout_seconds
            if refreshed.enable_mock_engine and "mock" not in engines:
                engines["mock"] = MockEngine()
            if not refreshed.enable_mock_engine and "mock" in engines:
                engines.pop("mock")
                manager.engines.pop("mock", None)
            return get_settings()

    @app.put("/api/kiosapi/secret")
    def set_secret(body: SecretUpdate) -> dict[str, bool]:
        secrets.set("kios_api_key", body.api_key)
        redactor.extend([body.api_key])
        return {"configured": True}

    @app.delete("/api/kiosapi/secret")
    def delete_secret() -> dict[str, bool]:
        secrets.delete("kios_api_key")
        return {"configured": False}

    @app.post("/api/kiosapi/probe")
    def probe(body: ProbeBody) -> dict[str, object]:
        current: Settings = app.state.settings
        key = secrets.get("kios_api_key")
        prepared = prepare_request(current, body.prompt, key, stream=False)
        shape = prepared.redacted(redactor)
        if body.dry_run:
            return {
                "live": False,
                "verified": False,
                "message": "Dry run only. No request was sent to KiosAPI.",
                "request": shape,
                "endpoints": plan_endpoints(current).public(),
            }
        if not key:
            raise HTTPException(status_code=409, detail={"code": "auth", "message": "KiosAPI credential is not configured."})
        try:
            result = KiosClient(current).complete(body.prompt, key)
        except KiosError as exc:
            return {
                "live": True,
                "verified": False,
                "error": exc.public(),
                "request": shape,
            }
        return {
            "live": True,
            "verified": True,
            "text": redactor.redact(str(result.get("text", ""))),
            "status": result.get("status"),
            "request": shape,
        }

    @app.get("/api/skills")
    def list_skills(project: str | None = None) -> dict[str, object]:
        installed = {}
        if project:
            try:
                resolved = resolve_project(project, _roots(app), secrets_path=secrets.path)
            except PathRejected as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            installed = installed_map(resolved)
        return {
            "skills": [skill.public() for skill in catalog.list()],
            "installed": installed,
        }

    @app.post("/api/skills/install")
    def install(body: SkillInstall) -> dict[str, object]:
        try:
            return install_skill(
                catalog,
                body.name,
                Path(body.project),
                body.engines,
                allowed_roots=_roots(app),
                secrets_path=secrets.path,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill not found") from exc
        except PathRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/skills/uninstall")
    def uninstall(body: SkillInstall) -> dict[str, object]:
        try:
            return uninstall_skill(
                body.name,
                Path(body.project),
                allowed_roots=_roots(app),
                secrets_path=secrets.path,
            )
        except PathRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/validate")
    def validate_project(body: ProjectBody) -> dict[str, str]:
        try:
            resolved = resolve_project(body.path, _roots(app), secrets_path=secrets.path)
        except PathRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"path": str(resolved)}

    @app.get("/api/sessions")
    def sessions() -> dict[str, object]:
        return {"sessions": manager.list_sessions()}

    @app.get("/api/tasks")
    def tasks() -> dict[str, object]:
        return {"tasks": manager.list_tasks()}

    @app.post("/api/tasks")
    def create_task(body: TaskBody) -> JSONResponse:
        try:
            project = str(resolve_project(body.project, _roots(app), secrets_path=secrets.path))
        except PathRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.engine_id not in engines:
            raise HTTPException(status_code=404, detail="engine not found")
        try:
            created = manager.submit(
                engine_id=body.engine_id,
                prompt=body.prompt,
                project=project,
                skills=body.skills,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(created, status_code=201)

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str) -> dict[str, object]:
        try:
            return manager.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> dict[str, object]:
        try:
            return manager.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: str) -> StreamingResponse:
        try:
            manager.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

        def stream():
            sent = 0
            while True:
                current = manager.get(task_id)
                events = current["events"]
                if isinstance(events, list):
                    while sent < len(events):
                        yield f"data: {json.dumps(events[sent])}\n\n"
                        sent += 1
                if current["status"] not in {"queued", "running"} and sent >= len(events if isinstance(events, list) else []):
                    yield f"event: end\ndata: {json.dumps({'status': current['status']})}\n\n"
                    break
                import time

                time.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/diagnostics")
    def diagnostics() -> dict[str, object]:
        mode = oct(secrets.path.stat().st_mode & 0o777)
        return doctor(
            {
                "secret_file_mode": mode,
                "bind": host,
                "inject_ui_token": inject_ui_token,
                "data_dir": str(data),
            }
        )

    return app


def _engine(engines: dict[str, object], engine_id: str):
    try:
        return engines[engine_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="engine not found") from exc


def _roots(app: FastAPI) -> list[Path]:
    settings: Settings = app.state.settings
    return [Path(item) for item in settings.allowed_roots]


def _validate_roots(roots: list[str], secrets_path: Path) -> None:
    for raw in roots:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise HTTPException(status_code=422, detail="allowed roots must be absolute")
        resolved = path.resolve()
        if resolved == Path("/") or str(resolved) in {"/etc", "/proc", "/sys", "/dev"}:
            raise HTTPException(status_code=422, detail="refusing an overly broad allowed root")
        if secrets_path.resolve().is_relative_to(resolved) and resolved != secrets_path.parent:
            # Allow the data directory itself to be a root only if secrets are not inside a project users will select.
            continue


def _authorized(request: Request, token: str, bind_host: str) -> bool:
    if not token:
        return False
    presented = _presented_token(request)
    if presented and hmac.compare_digest(presented, token):
        return True
    client = request.client.host if request.client else ""
    loopback = client in {"127.0.0.1", "::1"}
    return loopback and bind_host in {"127.0.0.1", "::1"} and os.environ.get("NEXUS_LOOPBACK_OPEN", "1") == "1"


def _presented_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    header_token = request.headers.get("x-nexus-token")
    if header_token:
        return header_token.strip()
    query = request.query_params.get("token")
    return query.strip() if query else ""


def _open_path(path: str) -> bool:
    return path == "/api/health" or path.startswith("/static/")


def _error_payload(exc: Exception, redactor: Redactor) -> dict[str, str]:
    code = getattr(exc, "code", "engine_error")
    message = getattr(exc, "message", str(exc))
    return {"code": str(code), "message": redactor.redact(str(message))}


def _isolate(engine_id: str, exc: Exception, redactor: Redactor) -> None:
    # The exception is returned to the caller. Other engines are untouched because
    # each adapter owns its process and this function does not stop them.
    redactor.redact(f"{engine_id}:{exc}")


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
