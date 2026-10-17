"""Which Prometheus an agent's copy of the skill came from.

The version is `<pyproject version>+<short commit>[.dirty] <hash>`: the commit is the last that changed the
skill or the templates (so other commits leave agents current), `.dirty` marks uncommitted changes to them, and
the hash is of their files, what a refresh copies. Only the hash decides whether an agent is up to date
(up_to_date): a refresh from uncommitted changes, later committed unchanged, stays current; the rest is for
people. A refresh writes the version to `.pi/skills/agent/VERSION` in the agent, inside the copy it replaces; a
copy that has the file reports it as it is (an agent's own scripts never compute a new one). Stdlib only.
"""
import hashlib
import importlib.metadata
import subprocess
import tomllib
from pathlib import Path

VERSION_FILE = "VERSION"
SKILL = Path(__file__).resolve().parent.parent


def content_hash(*folders: Path) -> str:
    """A hash of the files in `folders` (not caches, not the stamp), by path and bytes."""
    digest = hashlib.sha256()
    for folder in folders:
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            rel = path.relative_to(folder)
            if "__pycache__" in rel.parts or path.suffix == ".pyc" or path.name in (VERSION_FILE, ".DS_Store"):
                continue
            digest.update(folder.name.encode() + b"/" + rel.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def git(root: Path, *args: str):
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def installed_version():
    """The installed prometheus package's version (`uv tool install`), from its metadata; None if not installed."""
    try:
        return importlib.metadata.version("prometheus")
    except importlib.metadata.PackageNotFoundError:
        return None


def current(skill: Path = SKILL):
    """The version of the skill at `skill`: its stamp, else computed from Prometheus's repo (or the installed package,
    which keeps the repo's layout under prometheus/ but no pyproject.toml); None if neither."""
    stamp = skill / VERSION_FILE
    if stamp.is_file():
        return stamp.read_text(encoding="utf-8").strip() or None
    root = skill.parents[2]
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except OSError:
        installed = root.name == "prometheus" and (root / "__init__.py").is_file() and installed_version()
        project = {"name": "prometheus", "version": installed} if installed else {}
    except ValueError:
        return None
    if project.get("name") != "prometheus" or not project.get("version"):
        return None
    sources = sources_of(skill)
    paths = [str(d.relative_to(root)) for d in sources]
    # the last commit that changed what a refresh copies: a docs or tests commit leaves agents current
    sha = git(root, "log", "-1", "--format=%h", "--", *paths)
    dirty = sha and git(root, "status", "--porcelain", "--", *paths)
    label = f"{project['version']}+{sha}{'.dirty' if dirty else ''}" if sha else project["version"]  # no git: no commit
    return f"{label} {content_hash(*sources)[:8]}"


def sources_of(skill: Path = SKILL):
    """What a refresh copies: the skill, and the templates beside it (outside .pi)."""
    return [skill, *(d for d in [skill.parents[2] / "templates"] if d.is_dir())]


def up_to_date(installed_version, current_version):
    """Whether an agent stamped `installed_version` has what a refresh from this prometheus would copy: the
    same files, by hash (a version without a hash is not)."""
    hash_of = lambda v: (v or "").split(" ")[1:]
    return bool(hash_of(installed_version)) and hash_of(installed_version) == hash_of(current_version)


def installed(agent_dir: Path):
    """The version an agent was last refreshed to, or None (never stamped)."""
    stamp = Path(agent_dir) / ".pi" / "skills" / "agent" / VERSION_FILE
    return stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else None
