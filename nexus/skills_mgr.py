"""Read-only Skills catalog plus engine install paths.

Original SKILL.md files are never rewritten. Installation creates a symlink
(or a copy only when the platform cannot symlink) in the directory each engine
already knows how to scan.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .paths import PathRejected, resolve_project

OPENCODE_NAME = __import__("re").compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    directory: Path
    skill_file: Path
    category: str
    relative: str
    digest: str
    opencode_name_ok: bool
    extra_files: tuple[str, ...]

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "relative": self.relative,
            "digest": self.digest,
            "compatibility": {
                "free-claude-code": {
                    "ok": True,
                    "install_path": f".claude/skills/{self.name}/SKILL.md",
                    "note": "Claude Code and Free Claude Code harnesses discover Agent Skills in .claude/skills/<name>/SKILL.md. The file is not rewritten.",
                },
                "opencode": {
                    "ok": self.opencode_name_ok,
                    "install_path": f".opencode/skills/{self.name}/SKILL.md",
                    "note": (
                        "OpenCode discovers the same SKILL.md format in .opencode/skills, .claude/skills, and .agents/skills. "
                        "The id is the directory name."
                        if self.opencode_name_ok
                        else "OpenCode requires the skill name to match ^[a-z0-9]+(-[a-z0-9]+)*$ and the directory name. This skill is listed but not installed into OpenCode."
                    ),
                },
            },
        }


class SkillCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._skills = {skill.name: skill for skill in _load(root)}

    def list(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda item: (item.category, item.name))

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(name) from exc

    def digest_map(self) -> dict[str, str]:
        return {skill.relative: skill.digest for skill in self._skills.values()}


def install_skill(
    catalog: SkillCatalog,
    name: str,
    project: Path,
    engines: list[str],
    *,
    allowed_roots: list[Path],
    secrets_path: Path,
) -> dict[str, object]:
    project = resolve_project(str(project), allowed_roots, secrets_path=secrets_path)
    skill = catalog.get(name)
    before = _sha256(skill.skill_file)
    installed: list[str] = []
    skipped: list[dict[str, str]] = []
    for engine in engines:
        if engine == "opencode" and not skill.opencode_name_ok:
            skipped.append({"engine": engine, "reason": "name is not a valid OpenCode skill id"})
            continue
        if engine not in {"free-claude-code", "opencode"}:
            skipped.append({"engine": engine, "reason": "engine does not consume this skill format"})
            continue
        target = _install_target(project, engine, skill.name)
        _link_directory(skill.directory, target)
        installed.append(str(target))
    after = _sha256(skill.skill_file)
    if before != after:
        raise RuntimeError("skill install modified the original SKILL.md")
    record = _record_path(project)
    current = _read_record(record)
    current[skill.name] = {
        "source": str(skill.directory),
        "digest": skill.digest,
        "targets": installed,
    }
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return {
        "name": skill.name,
        "project": str(project),
        "installed": installed,
        "skipped": skipped,
        "original_unchanged": True,
    }


def uninstall_skill(
    name: str,
    project: Path,
    *,
    allowed_roots: list[Path],
    secrets_path: Path,
) -> dict[str, object]:
    project = resolve_project(str(project), allowed_roots, secrets_path=secrets_path)
    record_path = _record_path(project)
    current = _read_record(record_path)
    entry = current.get(name)
    removed: list[str] = []
    if isinstance(entry, dict):
        targets = entry.get("targets")
        if isinstance(targets, list):
            for target in targets:
                path = Path(str(target))
                if path.is_symlink() or path.exists():
                    _remove_install(path)
                    removed.append(str(path))
    current.pop(name, None)
    if current:
        record_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    elif record_path.exists():
        record_path.unlink()
    return {"name": name, "project": str(project), "removed": removed}


def installed_map(project: Path) -> dict[str, object]:
    return _read_record(_record_path(project))


def _load(root: Path) -> list[Skill]:
    skills: list[Skill] = []
    if not root.exists():
        return skills
    for skill_file in sorted(root.rglob("SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        meta, _body = _split_frontmatter(text)
        name = str(meta.get("name") or skill_file.parent.name).strip()
        description = str(meta.get("description") or "").strip()
        relative = str(skill_file.relative_to(root))
        category = relative.split("/", 1)[0]
        extras = tuple(
            sorted(
                str(path.relative_to(skill_file.parent))
                for path in skill_file.parent.rglob("*")
                if path.is_file() and path != skill_file
            )
        )
        skills.append(
            Skill(
                name=name,
                description=description,
                directory=skill_file.parent,
                skill_file=skill_file,
                category=category,
                relative=relative,
                digest=_sha256(skill_file),
                opencode_name_ok=bool(OPENCODE_NAME.fullmatch(name)) and name == skill_file.parent.name,
                extra_files=extras,
            )
        )
    return skills


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the Agent Skills frontmatter keys NEXUS needs without rewriting the file."""

    if not text.startswith("---"):
        return {}, text
    marker = text.find("\n---", 3)
    if marker == -1:
        return {}, text
    block = text[3:marker]
    body = text[marker + 4 :]
    meta: dict[str, object] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in {"name", "description"}:
            continue
        meta[key] = value.strip().strip("\"'")
    return meta, body


def _install_target(project: Path, engine: str, name: str) -> Path:
    if engine == "free-claude-code":
        return project / ".claude" / "skills" / name
    if engine == "opencode":
        return project / ".opencode" / "skills" / name
    raise PathRejected(f"unknown engine {engine}")


def _link_directory(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        if target.resolve() == source.resolve():
            return
        target.unlink()
    elif target.exists():
        raise PathRejected(f"{target} already exists and was not created by NEXUS")
    try:
        os.symlink(source.resolve(), target, target_is_directory=True)
    except OSError:
        shutil.copytree(source, target)
        marker = target / ".nexus-copied"
        marker.write_text("copy fallback; original was not modified\n", encoding="utf-8")


def _remove_install(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    marker = path / ".nexus-copied"
    if path.is_dir() and marker.exists():
        shutil.rmtree(path)


def _record_path(project: Path) -> Path:
    return project / ".nexus" / "skill-installs.json"


def _read_record(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()
