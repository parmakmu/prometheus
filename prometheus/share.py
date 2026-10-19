"""Sharing agents through git: any remote works (a GitLab project, another host, a folder).

    install <url | catalog name>   clone into the agents folder, then refresh it from your prometheus, so everything
                                   prometheus owns (its extension, the skill copy, the template files) is your own
                                   trusted copy; what is left is the agent's own code, shown for review before it stays
    install <url | name>@v1        on a machine that only runs it: the newest 1.x release, frozen (only its tag
                                   fetched, unpacked into the releases folder, no repository kept, nothing refreshed:
                                   it runs as released); its own code shown for review the same way
    pull <slug>                    the upstream's new commits, shown, then fast-forwarded onto main (and refreshed)
    share <slug> <remote>          checked for secrets and run data, then main and its releases pushed
    propose <slug>                 the checked-out change branch pushed (on GitLab, a merge request opened)

The catalog is `catalog.yaml` beside prometheus's files (PROMETHEUS_CATALOG names another): agents others can install
by name. Adding one is a change to prometheus's repo, reviewed like any other.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ruamel.yaml import YAML

from prometheus import cli
from prometheus.cli import PrometheusError
from prometheus.settings import settings

REFRESH_COMMIT = "Refresh from prometheus"  # agent_scaffold.py's message: install and pull make these locally


def git(folder: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise PrometheusError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r


def read_catalog() -> list[dict]:
    path = settings().catalog or cli.find_files() / "catalog.yaml"
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return []
    return [a for a in data.get("agents") or [] if isinstance(a, dict) and a.get("name") and a.get("url")]


def owned_by_prometheus(rel: str) -> bool:
    """Whether a refresh rewrites this file: the template's files and the skill copy (the plan itself is the agent's)."""
    scaffold = cli.import_script("agent_scaffold")
    return rel.startswith(".pi/skills/agent/") or (rel in {str(p) for p in scaffold.template_files()} and rel != "plan.yaml")


def list_own_code(folder: Path) -> list[tuple[str, int]]:
    """The agent's own files that run or reach a model (gates, programs, subagent profiles), with their line counts;
    a frozen release's from its folder (its repository is empty)."""
    files = git(folder, "ls-files").stdout.splitlines() or \
        sorted(str(f.relative_to(folder)) for f in folder.rglob("*") if f.is_file() and ".git" not in f.relative_to(folder).parts)
    own = [f for f in files if f.startswith(("gates/", "scripts/", ".pi/agents/", ".pi/extensions/")) and not owned_by_prometheus(f)
           and not f.endswith(".gitkeep")]
    return [(f, len((folder / f).read_text(encoding="utf-8", errors="replace").splitlines())) for f in own]


def list_own_packages(folder: Path) -> list[str]:
    """The packages the agent added to its pyproject.toml beyond the template's."""
    import tomllib
    try:
        have = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        base = tomllib.loads((cli.find_files() / "templates" / "agent" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    except (OSError, KeyError, ValueError):
        return []
    name = cli.import_script("utils").package_name
    return [d for d in have if name(d) not in {name(b) for b in base}]


def read_lessons(folder: Path) -> str:
    """What /agent-improve wrote into the agent's role from its runs (it goes into every turn's prompt)."""
    heading = cli.import_script("agent_scaffold").LESSONS_HEADING
    text = (folder / ".pi" / "APPEND_SYSTEM.md").read_text(encoding="utf-8") if (folder / ".pi" / "APPEND_SYSTEM.md").is_file() else ""
    return text[text.index(heading) + len(heading):].strip() if heading in text else ""


def describe(folder: Path) -> dict:
    """What a person should look at before trusting (install, pull) or publishing (share) an agent."""
    utils = cli.import_script("utils")
    plan = utils.read_plan(folder, {}) or {}
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict)]
    return {"goal": plan.get("goal"), "stages": len(stages),
            "side_effects": [s.get("id") for s in stages if utils.stage_kind(s) == "side_effect"],
            "own_code": list_own_code(folder), "packages": list_own_packages(folder), "lessons": read_lessons(folder)}


def refresh(folder: Path, lock: bool = True) -> dict:
    """`lock`: write uv.lock, which resolves (and may build) the agent's packages: only for an agent you trust."""
    r = cli.run_script("agent_scaffold.py", folder.name, "--root", str(folder.parent), "--refresh", *([] if lock else ["--no-lock"]))
    if not r.get("ok"):
        raise PrometheusError(f"refreshing {folder.name} failed: {r.get('error')}")
    return r


def install(source: str, slug: str | None = None, confirm=lambda info: True) -> Path:
    """Clone an agent (a catalog name, a URL or a folder), refresh it from this prometheus, and keep it only if
    `confirm(describe(...))` says so; returns its folder. With "@v1" (or "@v1.2"), a release instead, frozen
    (install_release)."""
    from prometheus.run_release import VERSION_RE
    head, at, tail = source.rpartition("@")
    source, version = (head, tail) if at and VERSION_RE.match(tail) else (source, None)  # git@host:… is a URL
    entry = next((a for a in read_catalog() if a["name"] == source), None)
    url = entry["url"] if entry else source
    slug = slug or (entry and entry["name"]) or re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
    scaffold = cli.import_script("agent_scaffold")
    if not scaffold.SLUG_RE.match(slug) or slug in scaffold.RESERVED:
        raise PrometheusError(f"{slug!r} can't name an agent: pass another with --as")
    if version:
        return install_release(url, slug, version, confirm)
    agents = cli.get_setting("agents")[0]
    folder = agents / slug
    if folder.exists():
        raise PrometheusError(f"{folder} exists: `prometheus pull {slug}` updates it, or install with --as <another name>")
    agents.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(["git", "clone", "-q", "--", url, str(folder)], capture_output=True, text=True, check=False)
    if clone.returncode != 0:
        raise PrometheusError(f"cannot clone {url}: {(clone.stderr or clone.stdout).strip()}")
    try:
        if not (folder / "plan.yaml").is_file():
            raise PrometheusError(f"{url} is not an agent: it has no plan.yaml")
        git(folder, "switch", "-q", "main")
        refresh(folder, lock=False)  # nothing of the agent's runs before you have seen it (uv lock may build its packages)
        if not confirm(describe(folder)):
            raise PrometheusError(f"not installed: {slug} removed")
        refresh(folder)  # trusted now: its uv.lock
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def install_release(url: str, slug: str, version: str, confirm=lambda info: True) -> Path:
    """The newest release of `url` matching `version`, frozen, for a machine that only runs it: only that tag is
    fetched (into a scratch repository, deleted after), unpacked into the releases folder as any release is
    (run_release.unpack), and kept only if `confirm(describe(...))` says so. Nothing is refreshed: it runs as released.
    Installed already: its folder, unchanged (a moved tag is refused)."""
    from prometheus import run_release
    listed = subprocess.run(["git", "ls-remote", "--tags", "--refs", "--", url, "v*"], capture_output=True, text=True, check=False)
    if listed.returncode != 0:
        raise PrometheusError(f"cannot read {url}: {(listed.stderr or listed.stdout).strip()}")
    tags = [line.split("refs/tags/", 1)[1] for line in listed.stdout.splitlines() if "refs/tags/" in line]
    versions = sorted((t for t in tags if re.match(r"^v\d+\.\d+$", t)), key=lambda t: tuple(map(int, t[1:].split("."))), reverse=True)
    wanted = run_release.pick_release([{"version": v} for v in versions], version)["version"]
    with tempfile.TemporaryDirectory() as scratch:
        repo = Path(scratch) / slug
        git(repo.parent, "init", "-q", str(repo))
        git(repo, "fetch", "-q", "--depth", "1", "--", url, f"refs/tags/{wanted}:refs/tags/{wanted}")
        release = next(r for r in cli.run_script("agent_release.py", "list", "--agent", str(repo))["releases"] if r["version"] == wanted)
        installed = run_release.release_folder(slug, release)
        if (installed / run_release.RELEASE_FILE).is_file():
            return run_release.unpack(repo, release, slug, source=url)  # here already (or refused: its tag moved)
        folder = run_release.unpack(repo, release, slug, source=url)
    try:
        if not confirm(describe(folder)):
            raise PrometheusError(f"not installed: {slug} {wanted} removed")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def check_clean_main(folder: Path, step: str):
    branch = git(folder, "branch", "--show-current").stdout.strip()
    if branch != "main":
        raise PrometheusError(f"{folder.name} has {branch or 'no branch'} checked out: {step} works on main (merge, keep or discard it first)")
    if git(folder, "status", "--porcelain").stdout.strip():
        raise PrometheusError(f"{folder.name} has uncommitted changes: {step} needs them committed or discarded")
    run = cli.import_script("run_folders").active_run(folder)
    if run:
        raise PrometheusError(f"{folder.name} has an active run ({run.id}): {step} after it ends")


def pull(slug: str, confirm=lambda info: True) -> dict:
    """Bring main up to the upstream's (`origin`), after `confirm` saw what changes. Local commits on main may only be
    refreshes (install and pull make them); anything else is a change to propose first."""
    folder = cli.get_agent(slug)
    check_clean_main(folder, "pull")
    git(folder, "fetch", "-q", "--tags", "origin")
    upstream = "origin/main"
    local = git(folder, "log", "--format=%s", f"{upstream}..main").stdout.splitlines()
    if any(not s.startswith(REFRESH_COMMIT) for s in local):
        raise PrometheusError(f"{slug}'s main has commits the upstream doesn't (besides refreshes): propose them "
                              f"(`prometheus propose {slug}` from their branch) or move them off main first")
    commits = git(folder, "log", "--format=%h %s", f"main..{upstream}").stdout.splitlines()
    if not commits:
        return {"updated": False, "commits": []}
    changed = git(folder, "diff", "--name-status", "main", upstream).stdout.splitlines()
    info = {"commits": commits, "changed": changed, "own_code_changed": [c for c in changed if not owned_by_prometheus(c.split("\t")[-1])]}
    if not confirm(info):
        return {"updated": False, "declined": True, **info}
    git(folder, "reset", "-q", "--hard", upstream)  # main had only refreshes on top: the upstream's, refreshed again below
    refresh(folder)
    return {"updated": True, **info, **describe(folder)}


def scan_for_secrets(folder: Path) -> list[str]:
    """Tracked files that look like they hold a secret, or a .env that git would push."""
    found = []
    for rel in git(folder, "ls-files").stdout.splitlines():
        path = folder / rel
        if Path(rel).name.startswith(".env"):
            found.append(f"{rel}: a values file (it may hold secrets) is in git")
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found += [f"{rel}: looks like {what}" for pattern, what in cli.import_script("utils").SECRET_PATTERNS if pattern.search(text)]
    return found + scan_history(folder, set(found))


def scan_history(folder: Path, found_now: set[str]) -> list[str]:
    """The same in the history a share pushes (main and the release tags): a secret deleted since is still in git."""
    found, commit, file = [], "", ""
    log = git(folder, "log", "-p", "--no-color", "--format=commit %h", "main", "--tags").stdout
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line.startswith("+++ b/"):
            file = line[6:]
            if Path(file).name.startswith(".env"):
                found.append(f"{file}: a values file (it may hold secrets) is in commit {commit}")
        elif line.startswith("+") and not line.startswith("+++"):
            found += [f"{file}: looks like {what}, in commit {commit}" for pattern, what in cli.import_script("utils").SECRET_PATTERNS
                      if pattern.search(line) and f"{file}: looks like {what}" not in found_now]
    return list(dict.fromkeys(found))


def share(slug: str, remote: str, confirm=lambda info: True) -> dict:
    """Push main and its releases to `remote` (made `origin`), after the scan and `confirm`."""
    folder = cli.get_agent(slug)
    check_clean_main(folder, "share")
    if secrets := scan_for_secrets(folder):
        raise PrometheusError("not shared, it may contain secrets: " + "; ".join(secrets))
    info = describe(folder)
    if not confirm(info):
        return {"shared": False, **info}
    has_origin = git(folder, "remote", "get-url", "origin", check=False).returncode == 0
    git(folder, "remote", "set-url" if has_origin else "add", "origin", remote)
    git(folder, "push", "-q", "-u", "origin", "main")
    releases = [t for t in git(folder, "tag", "-l", "v*").stdout.split() if t]
    if releases:
        git(folder, "push", "-q", "origin", *[f"refs/tags/{t}" for t in releases])
    return {"shared": True, "remote": remote, "releases": releases, **info}


def propose(slug: str) -> dict:
    """Push the checked-out change branch to origin; on GitLab, open a merge request into main as it is pushed."""
    folder = cli.get_agent(slug)
    branch = git(folder, "branch", "--show-current").stdout.strip()
    agent_repo = cli.import_script("agent_repo")
    if not agent_repo.is_change_branch(branch):
        raise PrometheusError(f"{slug} has {branch or 'no branch'} checked out: propose a change branch (/agent-revise or /agent-improve)")
    if git(folder, "status", "--porcelain").stdout.strip():
        raise PrometheusError(f"{slug} has uncommitted changes: finish the change first")
    utils = cli.import_script("utils")
    plan = utils.read_plan(folder, {}) or {}
    if why := agent_repo.why_not_ready(folder, plan):
        raise PrometheusError(f"the change isn't ready to propose: {why}")
    if git(folder, "remote", "get-url", "origin", check=False).returncode != 0:
        raise PrometheusError(f"{slug} has no upstream (origin): it was not installed or shared")
    mr = ["-o", "merge_request.create", "-o", "merge_request.target=main", "-o", f"merge_request.title={branch}"]
    pushed = git(folder, "push", "-u", "origin", branch, *mr, check=False)
    if pushed.returncode != 0 and "push options" in (pushed.stderr or ""):  # not GitLab: a plain push
        pushed = git(folder, "push", "-u", "origin", branch, check=False)
    if pushed.returncode != 0:
        raise PrometheusError(f"pushing {branch} failed: {pushed.stderr.strip()}")
    link = next((line.split()[-1] for line in pushed.stderr.splitlines() if "merge_requests" in line and "http" in line), None)
    return {"branch": branch, "merge_request": link}
