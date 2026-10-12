"""Integrity and environment checks. Missing upstream tests are recorded, not invented."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = REPO_ROOT / "third_party" / "UPSTREAM.json"

FCC_SYMBOLS = {
    "src/free_claude_code/cli/entrypoints.py": "def serve",
    "src/free_claude_code/config/custom_providers.py": "class CustomProviderDefinition",
    "src/free_claude_code/providers/custom.py": "class CustomProvider",
    "src/free_claude_code/api/routes.py": "/v1/messages",
    "src/free_claude_code/api/code_sessions_routes.py": "/admin/api/code/sessions",
    "src/free_claude_code/cli/launchers/opencode.py": "FCC manages OpenCode",
    "src/free_claude_code/core/rate_limit.py": "class StrictSlidingWindowLimiter",
}

OPENCODE_SYMBOLS = {
    "packages/cli/src/index.ts": "serve:",
    "packages/cli/src/commands/handlers/serve.ts": "ServerProcess.run",
    "packages/protocol/src/groups/session.ts": "/api/session/:sessionID/prompt",
    "packages/ai/src/providers/anthropic-compatible.ts": "anthropic-compatible",
    "packages/ai/src/providers/openai-compatible.ts": "openai-compatible",
    "packages/core/src/config/plugin/skill-file.ts": "SKILL.md",
    "packages/server/src/location.ts": "x-opencode-directory",
}


def doctor(extra: dict[str, object] | None = None) -> dict[str, object]:
    report: dict[str, object] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "node": _version(["node", "--version"]),
        "npm": _version(["npm", "--version"]),
        "opencode_bin": shutil.which("opencode"),
        "claude_bin": shutil.which("claude"),
        "fcc_python": os.environ.get("NEXUS_FCC_PYTHON"),
        "upstream": _upstream(),
        "secret_file_mode": extra.get("secret_file_mode") if extra else None,
        "bind": extra.get("bind") if extra else None,
    }
    return report


def _upstream() -> dict[str, object]:
    if not UPSTREAM.is_file():
        return {"ok": False, "error": "UPSTREAM.json missing"}
    manifest = json.loads(UPSTREAM.read_text(encoding="utf-8"))
    fcc_root = REPO_ROOT / "third_party" / "free-claude-code"
    skills_root = REPO_ROOT / "third_party" / "skills"
    opencode_root = REPO_ROOT / "third_party" / "opencode"
    fcc_files = manifest["free-claude-code"]["files"]
    skill_files = manifest["skills"]["files"]
    return {
        "ok": True,
        "free_claude_code": {
            "commit": manifest["free-claude-code"]["commit"],
            "license": manifest["free-claude-code"]["license"],
            "hashes_ok": _hashes_ok(fcc_root, fcc_files),
            "symbols_ok": _symbols_ok(fcc_root, FCC_SYMBOLS),
            "python_files": _count(fcc_root, "*.py"),
            "tests_present": (fcc_root / "tests").is_dir(),
        },
        "skills": {
            "commit": manifest["skills"]["commit"],
            "license": manifest["skills"]["license"],
            "hashes_ok": _hashes_ok(skills_root, skill_files),
            "skill_count": len(list(skills_root.rglob("SKILL.md"))),
        },
        "opencode": {
            "commit": manifest["opencode"]["commit"],
            "license": manifest["opencode"]["license"],
            "checkout_present": (opencode_root / "packages" / "cli" / "src" / "index.ts").is_file(),
            "symbols_ok": _symbols_ok(opencode_root, OPENCODE_SYMBOLS),
            "pinned_commit_present": _opencode_head(opencode_root) == manifest["opencode"]["commit"],
        },
    }


def _hashes_ok(root: Path, files: dict[str, str]) -> bool:
    for relative, expected in files.items():
        path = root / relative
        if not path.is_file() or _sha256(path) != expected:
            return False
    return True


def _symbols_ok(root: Path, symbols: dict[str, str]) -> bool:
    for relative, needle in symbols.items():
        path = root / relative
        if not path.is_file() or needle not in path.read_text(encoding="utf-8", errors="replace"):
            return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(root: Path, pattern: str) -> int:
    return sum(1 for _ in root.rglob(pattern))


def _version(argv: list[str]) -> str | None:
    try:
        completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or completed.stderr).strip()


def _opencode_head(root: Path) -> str | None:
    git = root / ".git"
    try:
        if git.is_file():
            pointer = git.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                gitdir = Path(pointer.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (root / gitdir).resolve()
                text = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
                return text if not text.startswith("ref:") else None
        text = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if text and not text.startswith("ref:") else None
