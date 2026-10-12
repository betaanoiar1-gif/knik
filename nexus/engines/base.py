"""Shared engine contract. Engine-specific features stay on the concrete adapter."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TaskUpdate:
    stream: str
    text: str


@dataclass
class EngineTask:
    task_id: str
    prompt: str
    project: str
    skills: list[str]
    cancel_event: threading.Event
    updates: list[TaskUpdate] = field(default_factory=list)

    def emit(self, stream: str, text: str) -> None:
        self.updates.append(TaskUpdate(stream, text))

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


class Engine(Protocol):
    id: str
    display_name: str

    def status(self) -> dict[str, object]: ...

    def start(self) -> dict[str, object]: ...

    def stop(self) -> dict[str, object]: ...

    def run(self, task: EngineTask) -> None: ...

    def cancel(self, task: EngineTask) -> None: ...
