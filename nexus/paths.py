"""Project-path checks. Engines only receive directories inside allowed roots."""

from __future__ import annotations

from pathlib import Path

DENIED_ROOTS = {
    Path("/"),
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/root"),
    Path("/boot"),
    Path("/run"),
}


class PathRejected(ValueError):
    pass


def resolve_project(raw: str, allowed_roots: list[Path], *, secrets_path: Path) -> Path:
    if not raw or "\x00" in raw:
        raise PathRejected("Project path is empty or contains a null byte.")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise PathRejected("Project path must be absolute.")
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise PathRejected(f"Project path cannot be resolved: {exc.__class__.__name__}") from exc
    if not resolved.exists() or not resolved.is_dir():
        raise PathRejected("Project path is not an existing directory.")
    real = resolved.resolve()
    if real in DENIED_ROOTS:
        raise PathRejected("That directory is too broad to use as a project.")
    secrets = secrets_path.resolve()
    if real == secrets or _is_relative_to(secrets, real):
        raise PathRejected("Project path would include the NEXUS secrets file.")
    for root in allowed_roots:
        try:
            root_real = root.expanduser().resolve()
        except OSError:
            continue
        if _is_relative_to(real, root_real):
            return real
    raise PathRejected("Project path is outside the allowed roots.")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
