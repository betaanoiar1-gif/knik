"""Subprocess helpers. Commands are argument lists, never a shell string."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path

from .redact import Redactor


class ManagedProcess:
    def __init__(self, process: subprocess.Popen[bytes], redactor: Redactor) -> None:
        self.process = process
        self.redactor = redactor
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, name=f"nexus-proc-{process.pid}", daemon=True)
        self._thread.start()

    @classmethod
    def spawn(
        cls,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        redactor: Redactor,
    ) -> ManagedProcess:
        if not argv or any("\x00" in arg for arg in argv):
            raise ValueError("refusing to spawn an empty or unsafe command")
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return cls(process, redactor)

    def _pump(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            text = self.redactor.redact(raw.decode("utf-8", "replace").rstrip("\n"))
            with self._lock:
                self.lines.append(text)
                if len(self.lines) > 5000:
                    del self.lines[:1000]

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.lines)

    def running(self) -> bool:
        return self.process.poll() is None

    def stop(self, timeout: float = 8) -> None:
        if self.process.poll() is not None:
            return
        pid = self.process.pid
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                return
            self.process.wait(timeout=3)
