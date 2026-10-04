"""Which Prometheus an agent's copy of the skill came from.

The version is `<pyproject version>+<short commit>` of Prometheus (the last commit that changed the
skill or the templates, so other commits leave agents current), and for uncommitted changes to
the skill or the templates `.dirty-<hash>`, a hash of their files, so two dirty refreshes with different content get
different versions and the same content gets the same one. A refresh writes it to
`.pi/skills/agent/VERSION` in the agent, inside the copy it replaces; a copy that has the
file reports it as it is (an agent's own scripts never compute a new one). Stdlib only.
"""
import hashlib
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


def current(skill: Path = SKILL):
    """The version of the skill at `skill`: its stamp, else computed from Prometheus's repo; None if neither."""
    stamp = skill / VERSION_FILE
    if stamp.is_file():
        return stamp.read_text(encoding="utf-8").strip() or None
    root = skill.parents[2]
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except (OSError, ValueError):
        return None
    if project.get("name") != "prometheus" or not project.get("version"):
        return None
    # what a refresh copies: the skill, and the templates beside it (outside .pi)
    sources = [skill, *(d for d in [root / "templates"] if d.is_dir())]
    paths = [str(d.relative_to(root)) for d in sources]
    # the last commit that changed what a refresh copies: a docs or tests commit leaves agents current
    sha = git(root, "log", "-1", "--format=%h", "--", *paths)
    if not sha:  # not a git repo, or the sources were never committed
        return f"{project['version']}+{content_hash(*sources)[:8]}"
    dirty = git(root, "status", "--porcelain", "--", *paths)
    return f"{project['version']}+{sha}" + (f".dirty-{content_hash(*sources)[:8]}" if dirty else "")


def installed(agent_dir: Path):
    """The version an agent was last refreshed to, or None (never stamped). An agent not refreshed since agents were
    called workflows keeps it in its old skill folder: read there, it shows as out of date rather than unknown."""
    for skill in ("agent", "workflow"):
        stamp = Path(agent_dir) / ".pi" / "skills" / skill / VERSION_FILE
        if stamp.is_file():
            return stamp.read_text(encoding="utf-8").strip()
    return None
