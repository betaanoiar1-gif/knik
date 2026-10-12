"""Run the upstream checks that this environment can actually execute.

Results are written to docs/UPSTREAM_TEST_RESULTS.json. A skipped suite is
recorded as skipped; it is not reported as passed.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "UPSTREAM_TEST_RESULTS.json"


def run(argv: list[str], *, cwd: Path | None = None, timeout: int = 120) -> dict[str, object]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "argv": argv, "error": f"{exc.__class__.__name__}: {exc}"}
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def main() -> None:
    skills = ROOT / "third_party" / "skills"
    fcc = ROOT / "third_party" / "free-claude-code"
    opencode = ROOT / "third_party" / "opencode"
    report: dict[str, object] = {
        "python": platform.python_version(),
        "node": shutil.which("node"),
        "npm": shutil.which("npm"),
        "bun": shutil.which("bun"),
        "opencode": shutil.which("opencode"),
        "checks": {},
    }
    checks = report["checks"]
    assert isinstance(checks, dict)
    checks["skills_list"] = run(["bash", "scripts/list-skills.sh"], cwd=skills)
    checks["skills_plugin_version"] = run(
        ["node", "scripts/sync-plugin-version.mjs", "--check"],
        cwd=skills,
    )
    fcc_python = shutil.which("python3.14")
    if fcc_python is None:
        checks["free_claude_code_pytest"] = {
            "status": "skipped",
            "reason": (
                "Upstream requires Python ==3.14.7 and uv. This environment has "
                f"Python {platform.python_version()} and no python3.14 binary. "
                "The vendored tests were not deleted; they were not executed."
            ),
            "test_path_present": (fcc / "tests").is_dir(),
        }
    else:
        checks["free_claude_code_pytest"] = run(
            [fcc_python, "-m", "pytest", "tests", "-q"],
            cwd=fcc,
            timeout=600,
        )
    if shutil.which("bun") is None:
        checks["opencode_package_tests"] = {
            "status": "skipped",
            "reason": (
                "OpenCode's workspace tests are invoked with bun "
                "(packages/cli/package.json script `bun test`). bun is not installed "
                "and the monorepo node_modules are not installed. The submodule tests "
                "were not deleted and were not executed."
            ),
            "cli_test_script": "bun test --timeout 30000 --only-failures",
        }
    else:
        checks["opencode_package_tests"] = run(
            ["bun", "test", "--timeout", "30000"],
            cwd=opencode / "packages" / "cli",
            timeout=600,
        )
    if shutil.which("opencode") is None:
        checks["opencode_binary"] = {
            "status": "not_installed",
            "reason": "No opencode binary on PATH. NEXUS can still launch one if NEXUS_OPENCODE_BIN is set.",
        }
    else:
        checks["opencode_binary"] = run(["opencode", "--version"])
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(OUT)
    print(json.dumps({key: value.get("status") if isinstance(value, dict) else value for key, value in checks.items()}, indent=2))


if __name__ == "__main__":
    main()
