"""Test double. It is never presented as Free Claude Code or OpenCode."""

from __future__ import annotations

import time

from .base import EngineTask


class MockEngine:
    id = "mock"
    display_name = "Mock engine (tests only)"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions: list[dict[str, str]] = []
        self._running = False
        self.fail_start = False

    def status(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.display_name,
            "kind": "test-double",
            "installed": True,
            "source_ok": True,
            "runtime_ready": True,
            "runtime_detail": "In-process test double. It does not call a model provider.",
            "credential_configured": False,
            "credential_verified": False,
            "running": self._running,
            "capabilities": ["test-double", "cancel", "stdout", "stderr"],
            "last_error": None,
            "real_engine": False,
        }

    def start(self) -> dict[str, object]:
        if self.fail_start:
            raise RuntimeError("mock start failed on purpose")
        self._running = True
        return self.status()

    def stop(self) -> dict[str, object]:
        self._running = False
        return self.status()

    def run(self, task: EngineTask) -> None:
        self.calls.append(task.prompt)
        session_id = f"mock-session-{len(self.sessions) + 1}"
        self.sessions.append({"id": session_id, "prompt": task.prompt})
        task.emit("system", f"engine=mock session={session_id}")
        if task.prompt.startswith("FAIL"):
            task.emit("stderr", "mock engine forced failure")
            raise RuntimeError("mock failure")
        delay = 30 if task.prompt.startswith("WAIT") else 0.01
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if task.cancelled():
                task.emit("system", "mock task cancelled")
                return
            time.sleep(0.01)
        task.emit("stdout", f"mock completed: {task.prompt}")

    def cancel(self, task: EngineTask) -> None:
        task.cancel_event.set()
