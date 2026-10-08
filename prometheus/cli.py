"""The `prometheus` command.

    prometheus                  the planner: pi in ~/.prometheus, with prometheus's extension, skill and role
    prometheus <slug> [args]    that agent: pi in its folder (args go to pi)
    prometheus init             where the agents and the planner's folder are (asks in a terminal)
    prometheus doctor           what prometheus needs, and what is missing
    prometheus release <slug>   tag a version of the agent that code can run (see release.py)
    prometheus run <slug>[@v]   run a release with no one to ask: --param name=value, --approve stage
    prometheus list             the agents and where each stands
    prometheus install <url>    an agent from git (or the catalog's name for it), reviewed before it stays
    prometheus pull <slug>      the agent's upstream changes, reviewed before main takes them
    prometheus share <slug> <remote>   push it (main and releases) for others to install
    prometheus propose <slug>   push its change branch upstream (on GitLab, as a merge request)
    prometheus catalog          the agents others shared by name
    prometheus refresh <slug|--all>    bring agents up to this prometheus's skill and template
    prometheus upgrade          update prometheus itself (uv tool upgrade)
    prometheus serve <slug>[@v]        serve one release over HTTP (see serve.py; /docs describes it)
    prometheus prune <slug>     delete old runs' files (the agent's and its releases'), keeping the newest

Each setting comes from, in order: a flag, an environment variable (PROMETHEUS_AGENTS, PROMETHEUS_HOME), the config
file (~/.config/prometheus/config.toml, written by init), a prompt (init, in a terminal only), the default.
"""
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

DEFAULTS = {"agents": "~/agents", "home": "~/.prometheus"}
COMMANDS = {"init", "doctor", "help", "release", "run", "list", "install", "pull", "share", "propose", "catalog",
            "refresh", "upgrade", "serve", "prune", "check"}  # scaffold_agent.RESERVED holds these (a test checks)
console = Console()
app = typer.Typer(add_completion=False, help=__doc__, rich_markup_mode=None)


def find_files() -> Path:
    """The folder holding prometheus's `.pi/` and `templates/`: the package itself when installed, the repo in a clone."""
    package = Path(__file__).resolve().parent
    for folder in (package, package.parent):
        if (folder / ".pi" / "skills" / "agent" / "SKILL.md").is_file():
            return folder
    raise SystemExit(f"prometheus's files are missing beside {package}: reinstall it")


class PrometheusError(Exception):
    """Something the caller must fix: no such agent or release, a missing tool, a refused step."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def scripts_dir() -> Path:
    """The skill's scripts: prometheus's own copy (the newest), used on any agent with --agent."""
    return find_files() / ".pi" / "skills" / "agent" / "scripts"


def run_script(script: str, *args: str) -> dict:
    """Run a skill script with this Python (it has their packages) and return its JSON."""
    r = subprocess.run([sys.executable, str(scripts_dir() / script), *args], capture_output=True, text=True, check=False)
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise PrometheusError(f"{script} failed: {(r.stderr or r.stdout).strip()[-2000:]}") from None


def import_script(name: str):
    """A skill script as a module (they import each other by name, from their folder)."""
    import importlib
    folder = str(scripts_dir())
    sys.path.insert(0, folder)
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(folder)


def get_config_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "prometheus" / "config.toml"


def read_config() -> dict:
    try:
        return tomllib.loads(get_config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_config(values: dict):
    path = get_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items()), encoding="utf-8")


def get_setting(name: str, flag: str | None = None) -> tuple[Path, str]:
    """A setting's folder and where it came from: flag, environment, config file or default."""
    for value, source in ((flag, "flag"), (os.environ.get(f"PROMETHEUS_{name.upper()}"), "environment"),
                          (read_config().get(name), "config"), (DEFAULTS[name], "default")):
        if value:
            return Path(value).expanduser().resolve(), source
    raise AssertionError(name)


def prepare_home(home: Path, files: Path):
    """The planner's folder: what pi reads only from a project's `.pi/` (pi-subagents' settings, the plan critic,
    linked so an upgrade needs nothing), and its sessions. The extension, skill and role come as pi flags."""
    pi_dir = home / ".pi"
    (pi_dir / "agents").mkdir(parents=True, exist_ok=True)
    if not (pi_dir / "settings.json").exists():
        shutil.copyfile(files / ".pi" / "settings.json", pi_dir / "settings.json")
    critic, target = pi_dir / "agents" / "plan-critic.md", files / ".pi" / "agents" / "plan-critic.md"
    if not (critic.is_symlink() and critic.readlink() == target):
        critic.unlink(missing_ok=True)
        critic.symlink_to(target)


def start_pi(cwd: Path, args: list[str], env: dict[str, str]):
    """Replace this process with pi in `cwd`."""
    pi = shutil.which("pi")
    if pi is None:
        raise SystemExit("pi is not installed: see `prometheus doctor`")
    os.chdir(cwd)
    os.execve(pi, [pi, *args], {**os.environ, **env})


def start_planner(args: list[str]):
    agents, _ = get_setting("agents")
    home, _ = get_setting("home")
    files = find_files()
    if not get_config_file().exists() and "PROMETHEUS_AGENTS" not in os.environ:
        if not sys.stdin.isatty():
            raise SystemExit("prometheus is not set up: run `prometheus init` (or set PROMETHEUS_AGENTS)")
        init(agents_flag=None, home_flag=None, yes=False)
        agents, _ = get_setting("agents")
        home, _ = get_setting("home")
    agents.mkdir(parents=True, exist_ok=True)
    prepare_home(home, files)
    pi_dir = files / ".pi"
    start_pi(home, [
        "-e", str(pi_dir / "extensions" / "prometheus" / "index.ts"),
        "--skill", str(pi_dir / "skills" / "agent"),
        "--append-system-prompt", str(pi_dir / "APPEND_SYSTEM.md"),
        "--append-system-prompt", f"The agents folder is {agents}.",
        *args,
    ], {"PROMETHEUS_AGENTS": str(agents), "PI_AGENT_PYTHON": sys.executable})


def get_agent(slug: str) -> Path:
    """The agent's folder in the agents folder; refused when there is no agent by that name."""
    agents, _ = get_setting("agents")
    folder = agents / slug
    if not (folder / "plan.yaml").is_file():
        raise PrometheusError(f"no agent {slug!r} in {agents}: `prometheus list` lists the agents")
    return folder


def start_agent(slug: str, args: list[str]):
    folder = get_agent(slug)
    # the agent's own Python (.venv, from its pyproject.toml), as when pi starts in its folder
    start_pi(folder, args, {"PROMETHEUS_AGENTS": str(folder.parent)})


@app.command()
def init(
    agents_flag: Annotated[str | None, typer.Option("--agents", help="folder for the agents (env PROMETHEUS_AGENTS)")] = None,
    home_flag: Annotated[str | None, typer.Option("--home", help="the planner's folder (env PROMETHEUS_HOME)")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="never ask: flags, environment, config or defaults")] = False,
):
    """Choose the agents folder and the planner's folder, and write them to the config file."""
    chosen = {}
    for name, flag, label in (("agents", agents_flag, "Agents folder"), ("home", home_flag, "Planner's folder")):
        folder, source = get_setting(name, flag)
        if source in ("config", "default") and sys.stdin.isatty() and not yes:
            folder = Path(Prompt.ask(label, default=str(folder), console=console)).expanduser().resolve()
        chosen[name] = folder
    for folder in chosen.values():
        folder.mkdir(parents=True, exist_ok=True)
    prepare_home(chosen["home"], find_files())
    write_config({name: str(folder) for name, folder in chosen.items()})
    found = sorted(p.parent.name for p in chosen["agents"].glob("*/plan.yaml"))
    console.print(f"Agents: {chosen['agents']}" + (f" ({len(found)} found: {', '.join(found)})" if found else ""))
    console.print(f"Planner: {chosen['home']}")
    console.print(f"Saved to {get_config_file()}")
    if not show_checks():
        console.print("Fix what is missing above, then run `prometheus`.")


def run_quiet(*cmd: str) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def find_pi_subagents() -> str | None:
    """pi-subagents' version, from the global Pi install."""
    agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent")
    try:
        return json.loads((agent_dir / "npm" / "node_modules" / "pi-subagents" / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return None


def show_checks() -> bool:
    """Print what prometheus needs, found or not (with the fix); whether all of it is there."""
    agents, agents_from = get_setting("agents")
    pi = run_quiet("pi", "--version")
    subagents = find_pi_subagents()
    uv = run_quiet("uv", "--version")
    node = run_quiet("node", "--version")
    git_name, git_email = run_quiet("git", "config", "user.name"), run_quiet("git", "config", "user.email")
    rows = [
        ("pi", pi, "install Pi: npm install -g @earendil-works/pi-coding-agent"),
        ("pi-subagents", subagents, "pi install npm:pi-subagents"),
        ("node", node, "install Node 22 or newer"),
        ("uv", uv and uv.removeprefix("uv "), "install uv: https://docs.astral.sh/uv/"),
        ("git identity", git_name and git_email and f"{git_name} <{git_email}>",
         'agents commit: git config --global user.name "…" and user.email "…"'),
        ("agents folder", agents.is_dir() and f"{agents} ({agents_from})", f"{agents} is missing: prometheus init"),
    ]
    table = Table(show_header=False, box=None)
    for name, found, fix in rows:
        table.add_row(name, f"[green]{found}[/]" if found else f"[red]missing[/]: {fix}")
    console.print(table)
    return all(found for _, found, _ in rows)


@app.command()
def doctor():
    """Check what prometheus needs: Pi, pi-subagents, Node, uv, a git identity, the agents folder."""
    raise typer.Exit(0 if show_checks() else 1)


@app.command()
def release(
    slug: Annotated[str, typer.Argument(help="the agent")],
    breaking: Annotated[bool, typer.Option("--breaking", help="the release can break callers: raise the major version")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="tag without asking")] = False,
):
    """Release the agent's current commit on main: check it, show its interface and what changed, then tag it."""
    folder = get_agent(slug)
    out = run_script("release.py", "check", "--agent", str(folder), *(["--breaking"] if breaking else []))
    face = out["interface"]
    params = ", ".join(f"{n} ({s['type']}{', required' if s['required'] else ''})" for n, s in face["params"].items())
    console.print(f"{slug}, plan revision {out['revision']}; last release: {out['last_release'] or 'none'}")
    console.print(f"  params: {params or 'none'}")
    console.print(f"  outputs: {', '.join(face['outputs']) or 'none'}; side effects callers approve: {', '.join(face['side_effects']) or 'none'}")
    console.print(f"  stages that use a model: {', '.join(out['model_stages']) or 'none'}")
    for change in out["changes"]["breaking"]:
        console.print(f"  [red]breaking[/]: {change}")
    for change in out["changes"]["other"]:
        console.print(f"  change: {change}")
    if out["already_released"]:
        console.print(f"Already released as {out['already_released']}.")
        raise typer.Exit(0)
    if out["problems"]:
        for problem in out["problems"]:
            console.print(f"  [red]can't release[/]: {problem}")
        raise typer.Exit(1)
    if not yes and not (sys.stdin.isatty() and Confirm.ask(f"Tag {out['next_version']}?", console=console)):
        raise typer.Exit(1)
    tagged = run_script("release.py", "tag", "--agent", str(folder), *(["--breaking"] if breaking else []))
    if not tagged.get("ok"):
        raise SystemExit(tagged.get("error"))
    console.print(f"Released {slug} {tagged['version']} ({tagged['commit'][:8]}).")


@app.command()
def check(slug: Annotated[str, typer.Argument(help="the agent")]):
    """Check the agent's plan.yaml, after editing it by hand: its errors and warnings, and the waves it would run."""
    out = run_script("validate_plan.py", str(get_agent(slug) / "plan.yaml"))
    for error in out["errors"]:
        console.print(f"[red]error[/]: {error}", highlight=False)
    for warning in out["warnings"]:
        console.print(f"[yellow]warning[/]: {warning}", highlight=False)
    waves = (out.get("summary") or {}).get("waves") or []
    shown = "; ".join(f"{w['id']} ({', '.join(w['stages'])}{'' if w['ends_with'] == 'none' else ', then ' + w['ends_with']})" for w in waves)
    console.print(f"{slug}: {'valid' if out['valid'] else 'not valid'}, status {out.get('status')}" + (f"; waves: {shown}" if shown else ""),
                  highlight=False)
    raise typer.Exit(0 if out["valid"] else 1)


def ask_needs(needs: dict) -> tuple[list[str], dict]:
    """Ask in the terminal what a paused run needs: each side effect to approve (what it writes), each decision."""
    approve, decisions = [], {}
    for a in needs.get("approve") or []:
        console.print(f"[bold]{a.get('title') or a['stage']}[/] ({a['stage']}): {a.get('why') or ''}")
        for path in a.get("writes") or []:
            console.print(f"  writes {path}")
        if Confirm.ask(f"Approve {a['stage']}?", console=console):
            approve.append(a["stage"])
        else:
            raise KeyboardInterrupt  # declined: the run is canceled
    for d in needs.get("decide") or []:
        console.print(f"[bold]{d.get('title') or d['stage']}[/] ({d['stage']})")
        for line in d.get("show_user") or []:
            console.print(f"  {line}")
        decisions[d["stage"]] = Prompt.ask("Your answer", console=console)
    return approve, decisions


@app.command("run")
def run_release(
    agent: Annotated[str, typer.Argument(help="the agent: a slug or a folder, with @v1 (newest 1.x) or @v1.2 (exactly)")],
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help="name=value, once per param (no secrets: those come from the environment)")] = None,
    approve: Annotated[list[str] | None, typer.Option("--approve", "-a", help="a side-effect stage the run may perform, once each")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="print events and the result as JSON lines")] = False,
):
    """Run a released agent from start to end, with no one to ask and no model deciding anything."""
    from prometheus.frozen import load_agent
    values = {}
    for item in param or []:
        name, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--param takes name=value, not {item!r}")
        values[name] = value
    try:
        loaded = load_agent(agent)
        started = loaded.start(params=values, approve=approve or [])
    except PrometheusError as exc:
        if as_json:
            print(json.dumps({"event": "refused", "error": str(exc)}))
        raise SystemExit(str(exc)) from None
    if not as_json:
        console.print(f"{loaded.name} {loaded.version}: run {started.id}")
    try:
        seen = 0
        while True:
            for n, event in enumerate(started.events()):
                if n < seen:
                    continue
                if as_json:
                    print(json.dumps(event), flush=True)
                elif event.get("event") == "child":
                    console.print(f"  {event['key']}: {event['state']}")
            seen = len(started.read_events())
            result = started.wait()
            if result.state != "input_required" or as_json or not sys.stdin.isatty():
                break
            started.answer(*ask_needs(result.needs))
    except KeyboardInterrupt:
        started.cancel()
        result = started.wait()
    summary = {"ok": result.ok, "state": result.state, "error": result.error, "cost_usd": result.cost_usd,
               "run_dir": str(result.run_dir), "outputs": {k: v and str(v) for k, v in result.outputs.items()},
               **({"needs": result.needs} if result.needs else {})}
    if result.state == "input_required" and not as_json:
        console.print("The run waits for an answer: " + "; ".join(
            [f"approve {a['stage']} (it writes {', '.join(a.get('writes') or [])})" for a in result.needs.get("approve") or []]
            + [f"decide {d['stage']}" for d in result.needs.get("decide") or []]) + ". Run it again with -a <stage> to approve up front.")
    if as_json:
        print(json.dumps({"event": "result", **summary}))
    else:
        console.print(("[green]done[/]" if result.ok else f"[red]{result.state}[/]: {result.error}") +
                      (f" (${result.cost_usd:.4f})" if result.cost_usd is not None else ""))
        for key, path in summary["outputs"].items():
            console.print(f"  {key}: {path}")
    raise typer.Exit(0 if result.ok else 1)


def show_review(info: dict, verb: str) -> bool:
    """Print what an agent would bring in or send out (its own code, packages, lessons); ask to go on (a terminal)."""
    if info.get("goal"):
        console.print(f"Goal: {info['goal']} ({info['stages']} stages; side effects: {', '.join(info['side_effects']) or 'none'})")
    for key in ("commits", "own_code_changed"):
        if info.get(key):
            console.print(("Upstream commits:" if key == "commits" else "Its own files that changed:") + "".join(f"\n  {c}" for c in info[key]))
    if info.get("own_code"):
        console.print("Its own code (runs on your machine; read it before you trust it):" +
                      "".join(f"\n  {path} ({lines} lines)" for path, lines in info["own_code"]))
    if info.get("packages"):
        console.print(f"Packages it adds: {', '.join(info['packages'])}")
    if info.get("lessons"):
        console.print(f"Lessons from its runs (in every turn's prompt):\n  {info['lessons'][:800]}")
    return sys.stdin.isatty() and Confirm.ask(f"{verb}?", console=console)


def ask_or_yes(yes: bool, verb: str):
    return (lambda info: True) if yes else (lambda info: show_review(info, verb))


@app.command("list")
def list_agents():
    """The agents and where each one stands."""
    agents, _ = get_setting("agents")
    found = run_script("list_agents.py", "--root", str(agents))
    table = Table("Agent", "State", "Runs", "Last run", "Skill")
    for a in found if isinstance(found, list) else []:
        last = a.get("last_run")
        table.add_row(a["slug"], f"{a.get('status')} (r{a.get('revision')})" + (f", running {a['active_run']}" if a.get("active_run") else ""),
                      str(a.get("runs", 0)), f"{last['id']} {last['status']}" if last else "-",
                      "update: prometheus refresh" if a.get("stale") else "current")
    console.print(table if table.rows else f"No agents in {agents}.")


@app.command()
def install(
    source: Annotated[str, typer.Argument(help="a git URL or folder, or a name from `prometheus catalog`")],
    name: Annotated[str | None, typer.Option("--as", help="the agent's slug here (default: the repo's name)")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="keep it without asking (you trust its source)")] = False,
):
    """Install an agent: clone it, replace what prometheus owns with your copy, then review its own code."""
    from prometheus import sharing
    folder = sharing.install(source, name, ask_or_yes(yes, "Keep it"))
    console.print(f"Installed {folder.name} in {folder}: `prometheus {folder.name}` starts it.")


@app.command()
def pull(slug: Annotated[str, typer.Argument(help="the agent")],
         yes: Annotated[bool, typer.Option("--yes", "-y", help="take the changes without asking")] = False):
    """Bring an installed agent up to its upstream's main, after showing what changed."""
    from prometheus import sharing
    out = sharing.pull(slug, ask_or_yes(yes, "Take these changes"))
    console.print(f"{slug}: " + ("updated." if out["updated"] else "kept as it was." if out.get("declined") else "already up to date."))
    raise typer.Exit(0 if out["updated"] or not out.get("declined") else 1)


@app.command()
def share(slug: Annotated[str, typer.Argument(help="the agent")],
          remote: Annotated[str, typer.Argument(help="where to push: a git URL (on GitLab, a new project is created on push if allowed)")],
          yes: Annotated[bool, typer.Option("--yes", "-y", help="push without asking")] = False):
    """Share an agent: check it for secrets, show what goes out, push main and its releases."""
    from prometheus import sharing
    out = sharing.share(slug, remote, ask_or_yes(yes, f"Push {slug} to {remote}"))
    if not out["shared"]:
        raise typer.Exit(1)
    console.print(f"Shared {slug} ({', '.join(out['releases']) or 'no releases yet'}). Others: `prometheus install {remote}`; "
                  "to list it by name, add it to prometheus's catalog.yaml with a merge request.")


@app.command()
def propose(slug: Annotated[str, typer.Argument(help="the agent, with its change branch checked out")]):
    """Propose the agent's change branch to its upstream (a merge request on GitLab)."""
    from prometheus import sharing
    out = sharing.propose(slug)
    console.print(f"Pushed {out['branch']}." + (f" Merge request: {out['merge_request']}" if out["merge_request"] else
                                                " Open a merge request into main on the upstream."))


@app.command()
def catalog():
    """The agents shared by name: `prometheus install <name>` installs one."""
    from prometheus import sharing
    table = Table("Name", "Description", "Source")
    for a in sharing.read_catalog():
        table.add_row(a["name"], a.get("description") or "", a["url"])
    console.print(table if table.rows else "The catalog is empty: share an agent, then add it to catalog.yaml with a merge request.")


@app.command()
def refresh(target: Annotated[str | None, typer.Argument(help="an agent's slug")] = None,
            every: Annotated[bool, typer.Option("--all", help="every agent whose skill is older than this prometheus's")] = False):
    """Bring agents up to this prometheus's skill and template (each commits on its own main)."""
    if bool(target) == every:
        raise SystemExit("name one agent, or --all")
    agents, _ = get_setting("agents")
    slugs = sorted(p.parent.name for p in agents.glob("*/plan.yaml")) if every else [target]
    for slug in slugs:
        out = run_script("scaffold_agent.py", slug, "--root", str(agents), "--refresh", *(["--if-stale"] if every else []))
        console.print(f"{slug}: " + ("current" if out.get("skipped") else "refreshed" if out.get("ok") else f"[red]{out.get('error')}[/]"))


@app.command()
def upgrade():
    """Update prometheus itself to the newest on its channel (uv tool upgrade); agents refresh separately."""
    raise typer.Exit(subprocess.run(["uv", "tool", "upgrade", "prometheus"], check=False).returncode)


@app.command()
def serve(
    agent: Annotated[str, typer.Argument(help="the agent: a slug or a folder, with @v1 or @v1.2 to pin a release")],
    host: Annotated[str, typer.Option(help="the address to listen on (off loopback, a token is required)")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="the port")] = 8080,
    token: Annotated[str | None, typer.Option(envvar="PROMETHEUS_SERVE_TOKEN", help="callers send Authorization: Bearer <token>")] = None,
    max_runs: Annotated[int, typer.Option(help="runs going at once; more are refused (429)")] = 4,
    max_upload_mb: Annotated[int, typer.Option(help="the largest request body")] = 100,
    keep: Annotated[int | None, typer.Option(help="after each run, prune finished runs beyond the newest N (their files; history stays)")] = None,
    public_url: Annotated[str | None, typer.Option(envvar="PROMETHEUS_PUBLIC_URL", help="where callers reach it, behind a proxy (default http://<host>:<port>): the A2A Agent Card and output links name it")] = None,
):
    """Serve one released agent over HTTP: start runs, follow them, download their outputs (one agent per process)."""
    import uvicorn

    from prometheus import serve as service
    from prometheus.frozen import load_agent
    service.check_host(host, token)
    loaded = load_agent(agent)
    console.print(f"Serving {loaded.name} {loaded.version} on http://{host}:{port} (/docs describes it)")
    app = service.make_app(loaded, token, max_runs, max_upload_mb, public_url or f"http://{host}:{port}", keep)
    uvicorn.run(app, host=host, port=port, access_log=False, log_level="warning")


@app.command()
def prune(
    slug: Annotated[str, typer.Argument(help="the agent")],
    keep: Annotated[int, typer.Option(help="finished runs to keep whole, in the agent and in each of its releases")] = 10,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="say what would go; delete nothing")] = False,
):
    """Delete the files of old finished runs (work, inputs, outputs, pi-subagents' records); their history stays."""
    from prometheus.frozen import get_cache
    agents, _ = get_setting("agents")
    folders = [f for f in [agents / slug, *sorted((get_cache() / "releases" / slug).glob(f"*/{slug}"))] if (f / "plan.yaml").is_file()]
    if not folders:
        raise SystemExit(f"no agent {slug!r} in {agents}, and no release of it in {get_cache()}")
    for folder in folders:
        out = run_script("run_history.py", "prune", "--agent", str(folder), "--keep", str(keep), *(["--dry-run"] if dry_run else []))
        where = "the agent" if folder == agents / slug else f"release {folder.parent.name}"
        console.print(f"{where}: {'would prune' if dry_run else 'pruned'} {len(out.get('pruned') or [])} runs "
                      f"({out.get('freed_mb', 0)} MB), {out.get('missions_removed', 0)} mission files; kept {len(out.get('kept') or [])}")


def main():
    args = sys.argv[1:]
    try:
        if not args or args[0].startswith("-") and args[0] not in ("--help", "-h"):
            start_planner(args)
        elif args[0] in COMMANDS or args[0] in ("--help", "-h"):
            app(args=["--help"] if args[0] == "help" else args, prog_name="prometheus")
        else:
            start_agent(args[0], args[1:])
    except PrometheusError as exc:
        raise SystemExit(str(exc)) from None
