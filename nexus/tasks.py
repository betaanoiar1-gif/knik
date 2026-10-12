"""Task and session ledger. Engine failures stay inside the task that caused them."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from .engines.base import Engine, EngineError, EngineTask
from .redact import Redactor


class TaskManager:
    def __init__(self, engines: dict[str, Engine], redactor: Redactor, data_dir: Path, *, max_concurrent: int, timeout: int) -> None:
        self.engines = engines
        self.redactor = redactor
        self.data_dir = data_dir
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self._lock = threading.Lock()
        self.tasks: dict[str, dict[str, object]] = {}
        self.sessions: dict[str, dict[str, object]] = {}
        self._running = 0
        self._threads: dict[str, threading.Thread] = {}
        self._load()

    def submit(self, *, engine_id: str, prompt: str, project: str, skills: list[str]) -> dict[str, object]:
        engine = self.engines.get(engine_id)
        if engine is None:
            raise KeyError(engine_id)
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is empty")
        with self._lock:
            if self._running >= self.max_concurrent:
                raise RuntimeError("too many concurrent tasks")
            task_id = uuid.uuid4().hex
            session_id = uuid.uuid4().hex
            record = {
                "id": task_id,
                "session_id": session_id,
                "engine_id": engine_id,
                "prompt": self.redactor.redact(prompt),
                "project": project,
                "skills": skills,
                "status": "queued",
                "error": None,
                "events": [],
                "created": time.time(),
                "updated": time.time(),
            }
            self.tasks[task_id] = record
            self.sessions[session_id] = {
                "id": session_id,
                "engine_id": engine_id,
                "project": project,
                "task_ids": [task_id],
                "created": record["created"],
            }
            self._running += 1
            cancel = threading.Event()
            engine_task = EngineTask(task_id, prompt, project, skills, cancel)
            thread = threading.Thread(
                target=self._execute,
                args=(record, engine, engine_task),
                name=f"nexus-task-{task_id[:8]}",
                daemon=True,
            )
            self._threads[task_id] = thread
            record["_cancel"] = cancel
            record["_engine_task"] = engine_task
            thread.start()
            self._persist()
            return self.public_task(record)

    def cancel(self, task_id: str) -> dict[str, object]:
        with self._lock:
            record = self.tasks.get(task_id)
            if record is None:
                raise KeyError(task_id)
            cancel = record.get("_cancel")
            engine_task = record.get("_engine_task")
            engine = self.engines.get(str(record["engine_id"]))
        if isinstance(cancel, threading.Event):
            cancel.set()
        if engine is not None and isinstance(engine_task, EngineTask):
            try:
                engine.cancel(engine_task)
            except Exception as exc:  # engine cancel must not take down the control plane
                self._append(record, "stderr", f"cancel error: {exc.__class__.__name__}")
        return self.public_task(record)

    def get(self, task_id: str) -> dict[str, object]:
        with self._lock:
            record = self.tasks.get(task_id)
            if record is None:
                raise KeyError(task_id)
            return self.public_task(record)

    def list_tasks(self) -> list[dict[str, object]]:
        with self._lock:
            items = [self.public_task(item) for item in self.tasks.values()]
        return sorted(items, key=lambda item: float(item["created"]), reverse=True)

    def list_sessions(self) -> list[dict[str, object]]:
        with self._lock:
            return sorted(self.sessions.values(), key=lambda item: float(item["created"]), reverse=True)

    def _execute(self, record: dict[str, object], engine: Engine, engine_task: EngineTask) -> None:
        record["status"] = "running"
        self._append(record, "system", f"started engine={record['engine_id']}")
        deadline = time.monotonic() + self.timeout
        try:
            worker = threading.Thread(target=self._engine_call, args=(engine, engine_task, record), daemon=True)
            worker.start()
            while worker.is_alive():
                self._drain(record, engine_task)
                if time.monotonic() > deadline:
                    engine_task.cancel_event.set()
                    try:
                        engine.cancel(engine_task)
                    except Exception:
                        pass
                    worker.join(timeout=5)
                    record["status"] = "failed"
                    record["error"] = "task timed out"
                    self._append(record, "stderr", "task timed out")
                    return
                worker.join(timeout=0.2)
            self._drain(record, engine_task)
            if engine_task.cancelled():
                record["status"] = "cancelled"
                self._append(record, "system", "task cancelled")
            elif record["status"] == "running":
                record["status"] = "succeeded"
                self._append(record, "system", "task succeeded")
        except Exception as exc:
            message = exc.message if isinstance(exc, EngineError) else str(exc)
            record["status"] = "failed"
            record["error"] = self.redactor.redact(message)
            self._append(record, "stderr", record["error"] or "task failed")
        finally:
            with self._lock:
                self._running = max(0, self._running - 1)
                record["updated"] = time.time()
                self._persist()

    def _engine_call(self, engine: Engine, engine_task: EngineTask, record: dict[str, object]) -> None:
        try:
            engine.run(engine_task)
        except Exception as exc:
            message = exc.message if isinstance(exc, EngineError) else f"{exc.__class__.__name__}: {exc}"
            record["status"] = "failed"
            record["error"] = self.redactor.redact(message)
            self._append(record, "stderr", record["error"] or "engine failed")

    def _drain(self, record: dict[str, object], engine_task: EngineTask) -> None:
        pending = list(engine_task.updates)
        engine_task.updates.clear()
        for update in pending:
            self._append(record, update.stream, update.text)

    def _append(self, record: dict[str, object], stream: str, text: str) -> None:
        event = {
            "stream": stream,
            "text": self.redactor.redact(text),
            "ts": time.time(),
        }
        events = record.setdefault("events", [])
        if isinstance(events, list):
            events.append(event)
        record["updated"] = time.time()

    def public_task(self, record: dict[str, object]) -> dict[str, object]:
        return {
            "id": record["id"],
            "session_id": record["session_id"],
            "engine_id": record["engine_id"],
            "prompt": record["prompt"],
            "project": record["project"],
            "skills": record["skills"],
            "status": record["status"],
            "error": record["error"],
            "events": record["events"],
            "created": record["created"],
            "updated": record["updated"],
        }

    def _persist(self) -> None:
        path = self.data_dir / "tasks.json"
        payload = {
            "tasks": [self.public_task(item) for item in self.tasks.values()],
            "sessions": list(self.sessions.values()),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _load(self) -> None:
        path = self.data_dir / "tasks.json"
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        for item in payload.get("tasks", []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                if item.get("status") in {"queued", "running"}:
                    item["status"] = "interrupted"
                    item["error"] = "NEXUS restarted before the task finished."
                self.tasks[item["id"]] = item
        for item in payload.get("sessions", []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                self.sessions[item["id"]] = item
