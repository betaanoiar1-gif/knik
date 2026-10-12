"""Engine adapters. Each engine keeps its own process, session namespace, and credentials."""

from .base import Engine, EngineError, TaskUpdate
from .fcc import FreeClaudeCodeEngine
from .mock import MockEngine
from .opencode import OpenCodeEngine

__all__ = [
    "Engine",
    "EngineError",
    "FreeClaudeCodeEngine",
    "MockEngine",
    "OpenCodeEngine",
    "TaskUpdate",
]
