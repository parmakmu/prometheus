"""The `prometheus` command.

    prometheus                  the planner: pi in ~/.prometheus, with prometheus's extension, skill and role
    prometheus <slug> [args]    that agent: pi in its folder (args go to pi)
    prometheus init             where the agents and the planner's folder are (asks in a terminal)
    prometheus doctor [<slug>[@v]]     what prometheus needs, and what is missing; with a release, whether it can run here
    prometheus check <slug>     validate the agent's plan.yaml: errors, warnings, the waves it would run
    prometheus release <slug>   tag a version of the agent that code can run (see agent_release.py)
    prometheus run <slug>[@v]   run a release with no one to ask: --param name=value, --approve stage
    prometheus runs <slug> [run]       a release's runs here, or how one went
    prometheus bundle <slug> <run>     pack a released run for /agent-improve where the agent is developed
    prometheus list             the agents and where each stands, and the releases installed here
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
file (~/.config/prometheus/config.toml, written by init), a prompt (init, in a terminal only), the default: see
settings.py, which lists them all.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from packaging.version import InvalidVersion, Version
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from prometheus import share as sharing  # `share` is also a command here
from prometheus.errors import PrometheusError
from prometheus.run_release import (
    list_installed,
    load_agent,
    read_bundle_call,
    state_of,
)
from prometheus.settings import (
    config_file,
    get_agent,
    get_setting,
    settings,
    write_config,
)
from prometheus.skill import (
    agent_params,
    agent_release,
    find_files,
    pi_records,
    run_folders,
    run_script,
    utils,
)

COMMANDS = {"init", "doctor", "help", "release", "run", "list", "install", "pull", "share", "propose", "catalog",
            "refresh", "upgrade", "serve", "prune", "check", "bundle", "runs"}  # agent_scaffold.RESERVED holds these (a test checks)
console = Console()
app = typer.Typer(add_completion=False, rich_markup_mode=None, help=(
    "Builds agents and runs them.\n\n"
    "prometheus starts the planner (/agent-new plans an agent); prometheus <slug> starts that agent. "
    "Settings come from a flag, the environment (PROMETHEUS_AGENTS, PROMETHEUS_HOME), "
    "~/.config/prometheus/config.toml (written by init), or the default."))


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
    if not config_file().exists() and "PROMETHEUS_AGENTS" not in os.environ:
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


def start_agent(slug: str, args: list[str]):
    folder = get_agent(slug)
    # the agent's own Python (.venv, from its pyproject.toml), as when pi starts in its folder
    start_pi(folder, args, {"PROMETHEUS_AGENTS": str(folder.parent)})


@app.command(short_help="choose the agents folder and the planner's folder", rich_help_panel="Setup")
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
    console.print(f"Saved to {config_file()}")
    if not show_checks():
        console.print("Fix what is missing above, then run `prometheus`.")


def run_quiet(*cmd: str) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def find_pi_subagents() -> str | None:
    """pi-subagents' version, from the global Pi install (pi_records.install)."""
    if (installed := pi_records.install()) is None:
        return None
    try:
        return json.loads((installed / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return None


# the versions prometheus is built and tested on: older ones wake the agent differently (notice turns, before_agent_start)
MIN_VERSIONS = {"pi": "1.3.0", "pi-subagents": "0.79.0"}


def check_version(name: str, found: str | None) -> tuple[str | None, str | None]:
    """(found, problem): the version found, and why it won't do: older than MIN_VERSIONS[name], or not a version."""
    if not found:
        return None, None
    try:
        version = Version(found.split()[-1])  # `pi --version` may name itself first
    except InvalidVersion:
        return found, f"{found} is not a version prometheus can read: needs {MIN_VERSIONS[name]} or later"
    return found, None if version >= Version(MIN_VERSIONS[name]) else f"{found} is too old: needs {MIN_VERSIONS[name]} or later"


def show_checks(running_only: bool = False) -> bool:
    """Print what prometheus needs, found or not (with the fix); whether all of it is there. `running_only`: for a
    machine that only runs releases, which commits nothing, so needs no git identity."""
    agents, agents_from = get_setting("agents")
    pi, pi_old = check_version("pi", run_quiet("pi", "--version"))
    subagents, subagents_old = check_version("pi-subagents", find_pi_subagents())
    uv = run_quiet("uv", "--version")
    node = run_quiet("node", "--version")
    git_name, git_email = run_quiet("git", "config", "user.name"), run_quiet("git", "config", "user.email")
    rows = [
        ("pi", not pi_old and pi, f"{pi_old or 'missing'}: npm install -g @earendil-works/pi-coding-agent"),
        ("pi-subagents", not subagents_old and subagents, f"{subagents_old or 'missing'}: pi install npm:pi-subagents (or pi update)"),
        ("node", node, "missing: install Node 22 or newer"),
        ("uv", uv and uv.removeprefix("uv "), "missing: install uv: https://docs.astral.sh/uv/"),
        ("git identity", git_name and git_email and f"{git_name} <{git_email}>",
         'missing: agents commit: git config --global user.name "…" and user.email "…"'),
        ("agents folder", agents.is_dir() and f"{agents} ({agents_from})", f"{agents} is missing: prometheus init"),
    ]
    return print_checks([row for row in rows if not (running_only and row[0] == "git identity")])


def print_checks(rows: list[tuple[str, str | None, str]]) -> bool:
    """A table of (what, what was found, the problem when nothing was); whether everything was found."""
    table = Table(show_header=False, box=None)
    for name, found, problem in rows:
        table.add_row(name, f"[green]{found}[/]" if found else f"[red]{problem}[/]")
    console.print(table)
    return all(found for _, found, _ in rows)


def check_release(agent: str) -> bool:
    """Print whether a release can run here: its secrets in the environment, its packages, the models it needs."""
    loaded = load_agent(agent)
    plan = utils.read_plan(loaded.folder, {}) or {}
    rows = [("release", f"{loaded.name} {loaded.version} ({loaded.release['commit'][:12]})", "")]
    for name, spec in (plan.get("params") or {}).items():
        if agent_params.kind(spec) == "secret":
            env = agent_params.env_name(name, spec)
            rows.append((f"secret {name}", os.environ.get(env) and f"{env} is set",
                         f"{env} is not set: a release reads secrets from the environment (it has no .env)"))
    try:
        sync = subprocess.run(["uv", "sync", "--locked", "--dry-run"], cwd=loaded.folder, capture_output=True, text=True,
                              timeout=300, check=False)
        problem = (sync.stderr or sync.stdout).strip().splitlines()[-1:] if sync.returncode else []
    except (OSError, subprocess.TimeoutExpired) as exc:
        problem = [str(exc)]
    rows.append(("packages", not problem and "uv.lock installs here", f"uv sync --locked --dry-run: {' '.join(problem)}"))
    model = agent_release.model_stages(plan)
    default = (plan.get("models") or {}).get("default") or "Pi's default model"
    rows.append(("model stages", f"{', '.join(model)}: a login or key for {default}" if model else "none: no model key needed", ""))
    return print_checks(rows)


@app.command(short_help="check what prometheus needs; with <slug>, whether its release can run here", rich_help_panel="Setup")
def doctor(agent: Annotated[str | None, typer.Argument(help="a release to check as well: <slug>[@v1 or @v1.2]")] = None):
    """Check what prometheus needs (Pi, pi-subagents, Node, uv, a git identity, the agents folder) and, given an agent,
    whether its release can run here (its secrets, its packages, the models it needs)."""
    ok = show_checks(running_only=agent is not None)
    if agent:
        console.print()
        ok = check_release(agent) and ok
    raise typer.Exit(0 if ok else 1)


@app.command(short_help="tag a version of an agent that code can run", rich_help_panel="Releases")
def release(
    slug: Annotated[str, typer.Argument(help="the agent")],
    breaking: Annotated[bool, typer.Option("--breaking", help="the release can break callers: raise the major version")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="tag without asking")] = False,
):
    """Release the agent's current commit on main: check it, show its interface and what changed, then tag it."""
    folder = get_agent(slug)
    out = run_script("agent_release.py", "check", "--agent", str(folder), *(["--breaking"] if breaking else []))
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
    tagged = run_script("agent_release.py", "tag", "--agent", str(folder), *(["--breaking"] if breaking else []))
    if not tagged.get("ok"):
        raise SystemExit(tagged.get("error"))
    console.print(f"Released {slug} {tagged['version']} ({tagged['commit'][:8]}).")


@app.command(short_help="check an agent's plan.yaml", rich_help_panel="Agents")
def check(slug: Annotated[str, typer.Argument(help="the agent")]):
    """Check the agent's plan.yaml, after editing it by hand: its errors and warnings, and the waves it would run."""
    out = run_script("plan_validate.py", str(get_agent(slug) / "plan.yaml"))
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


@app.command("run", short_help="run a release: <slug>[@v1], -p name=value, -a stage", rich_help_panel="Releases")
def run_release(
    agent: Annotated[str, typer.Argument(help="the agent: a slug or a folder, with @v1 (newest 1.x) or @v1.2 (exactly), or "
                                              "@<branch or commit> to run that commit as a build")],
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help="name=value, once per param (no secrets: those come from the environment)")] = None,
    approve: Annotated[list[str] | None, typer.Option("--approve", "-a", help="a side-effect stage the run may perform, once each")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="print events and the result as JSON lines")] = False,
    from_bundle: Annotated[Path | None, typer.Option("--bundle", help="run again what a run bundled --with-data was given: its "
                                                     "params and files (its side effects ask again); --param overrides")] = None,
):
    """Run a released agent from start to end, with no one to ask and no model deciding anything."""
    values = {}
    for item in param or []:
        name, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--param takes name=value, not {item!r}")
        values[name] = value
    try:
        loaded = load_agent(agent)
        in_run = set()
        if from_bundle:
            given, in_run = read_bundle_call(from_bundle, loaded)
            values = {**given, **values}
            in_run -= set(values)
        started = loaded.start(params=values, approve=list(approve or []), in_run=in_run)
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


@app.command("list", short_help="the agents and where each stands, and releases installed here", rich_help_panel="Agents")
def agent_list():
    """The agents and where each one stands, and the releases installed here (`prometheus install <source>@v1`)."""
    agents, _ = get_setting("agents")
    found = run_script("agent_list.py", "--root", str(agents))
    table = Table("Agent", "State", "Runs", "Last run", "Skill")
    for a in found if isinstance(found, list) else []:
        last = a.get("last_run")
        table.add_row(a["slug"], f"{a.get('status')} (r{a.get('revision')})" + (f", running {a['active_run']}" if a.get("active_run") else ""),
                      str(a.get("runs", 0)), f"{last['id']} {last['status']}" if last else "-",
                      "update: prometheus refresh" if a.get("stale") else "current")
    console.print(table if table.rows else f"No agents in {agents}.")
    releases = Table("Release", "Versions here", "Runs")
    for slug in sorted(p.name for p in settings().releases.glob("*") if p.is_dir()):
        if installed := list_installed(slug):
            releases.add_row(slug, ", ".join(r["version"] for r in installed), str(len(list_released_runs(slug))))
    if releases.rows:
        console.print(releases)


@app.command(short_help="install an agent from git, or one release of it (@v1)", rich_help_panel="Sharing")
def install(
    source: Annotated[str, typer.Argument(help="a git URL or folder, or a name from `prometheus catalog`; with @v1 or @v1.2, "
                                               "that release, frozen, for a machine that only runs it")],
    name: Annotated[str | None, typer.Option("--as", help="the agent's slug here (default: the repo's name)")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="keep it without asking (you trust its source)")] = False,
):
    """Install an agent: clone it, replace what prometheus owns with your copy, then review its own code. Or, with
    @<version>, install one release of it, frozen: no repository, run as released (`prometheus run`, `serve`)."""
    folder = sharing.install(source, name, ask_or_yes(yes, "Keep it"))
    if (folder / "RELEASE.json").is_file():
        record = json.loads((folder / "RELEASE.json").read_text(encoding="utf-8"))
        console.print(f"Installed {folder.name} {record['version']} ({record['commit'][:12]}), frozen, in {folder}: "
                      f"`prometheus run {folder.name}@{record['version']}` or `prometheus serve` runs it.", highlight=False)
        return
    console.print(f"Installed {folder.name} in {folder}: `prometheus {folder.name}` starts it.")


@app.command(short_help="take an installed agent's upstream changes", rich_help_panel="Sharing")
def pull(slug: Annotated[str, typer.Argument(help="the agent")],
         yes: Annotated[bool, typer.Option("--yes", "-y", help="take the changes without asking")] = False):
    """Bring an installed agent up to its upstream's main, after showing what changed."""
    out = sharing.pull(slug, ask_or_yes(yes, "Take these changes"))
    console.print(f"{slug}: " + ("updated." if out["updated"] else "kept as it was." if out.get("declined") else "already up to date."))
    raise typer.Exit(0 if out["updated"] or not out.get("declined") else 1)


@app.command(short_help="push an agent and its releases for others", rich_help_panel="Sharing")
def share(slug: Annotated[str, typer.Argument(help="the agent")],
          remote: Annotated[str, typer.Argument(help="where to push: a git URL (on GitLab, a new project is created on push if allowed)")],
          yes: Annotated[bool, typer.Option("--yes", "-y", help="push without asking")] = False):
    """Share an agent: check it for secrets, show what goes out, push main and its releases."""
    out = sharing.share(slug, remote, ask_or_yes(yes, f"Push {slug} to {remote}"))
    if not out["shared"]:
        raise typer.Exit(1)
    console.print(f"Shared {slug} ({', '.join(out['releases']) or 'no releases yet'}). Others: `prometheus install {remote}`; "
                  "to list it by name, add it to prometheus's catalog.yaml with a merge request.")


@app.command(short_help="push a change branch upstream (a merge request on GitLab)", rich_help_panel="Sharing")
def propose(slug: Annotated[str, typer.Argument(help="the agent, with its change branch checked out")]):
    """Propose the agent's change branch to its upstream (a merge request on GitLab)."""
    out = sharing.propose(slug)
    console.print(f"Pushed {out['branch']}." + (f" Merge request: {out['merge_request']}" if out["merge_request"] else
                                                " Open a merge request into main on the upstream."))


@app.command(short_help="agents others shared by name", rich_help_panel="Sharing")
def catalog():
    """The agents shared by name: `prometheus install <name>` installs one."""
    table = Table("Name", "Description", "Source")
    for a in sharing.read_catalog():
        table.add_row(a["name"], a.get("description") or "", a["url"])
    console.print(table if table.rows else "The catalog is empty: share an agent, then add it to catalog.yaml with a merge request.")


@app.command(short_help="bring agents up to this prometheus's skill and template", rich_help_panel="Agents")
def refresh(target: Annotated[str | None, typer.Argument(help="an agent's slug")] = None,
            every: Annotated[bool, typer.Option("--all", help="every agent whose skill is older than this prometheus's")] = False):
    """Bring agents up to this prometheus's skill and template (each commits on its own main)."""
    if bool(target) == every:
        raise SystemExit("name one agent, or --all")
    agents, _ = get_setting("agents")
    slugs = sorted(p.parent.name for p in agents.glob("*/plan.yaml")) if every else [target]
    for slug in slugs:
        out = run_script("agent_scaffold.py", slug, "--root", str(agents), "--refresh", *(["--if-stale"] if every else []))
        console.print(f"{slug}: " + ("current" if out.get("skipped") else "refreshed" if out.get("ok") else f"[red]{out.get('error')}[/]"))


@app.command(short_help="update prometheus itself", rich_help_panel="Setup")
def upgrade():
    """Update prometheus itself to the newest on its channel (uv tool upgrade); agents refresh separately."""
    raise typer.Exit(subprocess.run(["uv", "tool", "upgrade", "prometheus"], check=False).returncode)


@app.command(short_help="serve a release over HTTP and A2A", rich_help_panel="Releases")
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
    import uvicorn  # here, not at the top: the server's libraries would slow every other command (~200 ms)

    from prometheus import serve as service
    service.check_host(host, token)
    loaded = load_agent(agent)
    console.print(f"Serving {loaded.name} {loaded.version} on http://{host}:{port} (/docs describes it)")
    app = service.make_app(loaded, token, max_runs, max_upload_mb, public_url or f"http://{host}:{port}", keep)
    uvicorn.run(app, host=host, port=port, access_log=False, log_level="warning")


@app.command(short_help="delete old runs' files, keeping their history", rich_help_panel="Agents")
def prune(
    slug: Annotated[str, typer.Argument(help="the agent")],
    keep: Annotated[int, typer.Option(help="finished runs to keep whole, in the agent and in each of its releases")] = 10,
    keep_failed: Annotated[int, typer.Option(help="a release's failed runs to keep whole as well, for `prometheus bundle`")] = 10,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="say what would go; delete nothing")] = False,
):
    """Delete the files of old finished runs (work, inputs, outputs, pi-subagents' records); their history stays."""
    agents, _ = get_setting("agents")
    folders = [f for f in [agents / slug, *sorted((settings().releases / slug).glob("*"))] if (f / "plan.yaml").is_file()]
    if not folders:
        raise SystemExit(f"no agent {slug!r} in {agents}, and no release of it in {settings().releases}")
    for folder in folders:
        out = run_script("run_history.py", "prune", "--agent", str(folder), "--keep", str(keep), "--keep-failed", str(keep_failed),
                         *(["--dry-run"] if dry_run else []))
        where = "the agent" if folder == agents / slug else f"release {folder.name}"
        console.print(f"{where}: {'would prune' if dry_run else 'pruned'} {len(out.get('pruned') or [])} runs "
                      f"({out.get('freed_mb', 0)} MB), {out.get('missions_removed', 0)} mission files, "
                      f"{out.get('empty_session_folders', 0)} empty launch folders; kept {len(out.get('kept') or [])}")


def find_released_run(slug: str, run_id: str) -> Path:
    """A release's run folder, whichever release it ran."""
    found = [d for d in (settings().releases / slug).glob(f"*/runs/{run_id}") if (d / "run.yaml").is_file()]
    if not found:
        raise PrometheusError(f"no run {run_id!r} of a release of {slug} in {settings().releases / slug}")
    return found[0]


def list_released_runs(slug: str) -> list[dict]:
    """Every run of every release of `slug` here, oldest first: id, version (the one it was called as), state, start,
    cost, error."""
    read_json = utils.read_json
    found = []
    for folder in (settings().releases / slug).glob("*"):
        for run_id, run_dir in run_folders.list_runs(folder):
            state, call = run_folders.read_run(run_dir), read_json(run_dir / "call.json") or {}
            found.append({"run": run_id, "version": call.get("version"), "state": state_of(state), "started": state.get("started_at"),
                          "cost_usd": round(sum(x.get("cost_usd") or 0 for x in state.get("launches") or []), 4),
                          "error": state.get("error")})
    return sorted(found, key=lambda r: str(r["started"] or ""))


@app.command("runs", short_help="a release's runs here, or how one went", rich_help_panel="Releases")
def show_runs(
    slug: Annotated[str, typer.Argument(help="the agent")],
    run_id: Annotated[str | None, typer.Argument(help="a run: show how it went")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="print JSON")] = False,
):
    """The runs of an agent's releases on this machine, or how one went (what its bundle would say). Changes nothing."""
    if run_id is None:
        found = list_released_runs(slug)
        for r in found:
            r["started"] = r["started"] and datetime.datetime.fromisoformat(r["started"]).astimezone().isoformat(timespec="seconds")
        if as_json:
            print(json.dumps(found, indent=1))
            return
        if not found:
            console.print(f"No runs of a release of {slug} here.")
            return
        table = Table(box=None)
        for column in ("run", "version", "state", "started", "cost"):  # the run id is copied into commands: never cut
            table.add_column(column, no_wrap=True)
        table.add_column("error", overflow="fold")
        for r in found:
            color = {"complete": "green", "failed": "red", "error": "red", "input_required": "yellow"}.get(r["state"], "")
            table.add_row(r["run"], r["version"], f"[{color}]{r['state']}[/]" if color else str(r["state"]),
                          (r["started"] or "")[:16].replace("T", " "), f"${r['cost_usd']:.4f}", (r["error"] or "")[:80])
        console.print(table)
        return
    shown = run_script("run_bundle.py", "show", "--run", str(find_released_run(slug, run_id)))
    if not shown.get("ok"):
        raise PrometheusError(shown.get("error") or "run_bundle.py failed")
    shown["state"] = state_of({"status": shown.get("state")})  # what the run recorded, as callers see it
    if as_json:
        print(json.dumps(shown, indent=1))
        return
    console.print(f"[bold]{slug} {shown['version']}[/] run {run_id}: {shown['state']} (commit {(shown.get('commit') or '?')[:12]})", highlight=False)
    if shown.get("error"):
        console.print(f"[red]{shown['error']}[/]", highlight=False)
    for launch in shown.get("launches") or []:
        cost = f", ${launch['cost_usd']:.4f}" if launch.get("cost_usd") is not None else ""
        console.print(f"wave {launch.get('wave')} ({', '.join(launch.get('stages') or [])}): {launch.get('status')}{cost}", highlight=False)
        for child in launch.get("children") or []:
            if child.get("status") not in ("completed", "complete", "done"):
                console.print(f"  [red]{child.get('key')}[/] ({child.get('agent')}): {child.get('status')}"
                              f"{': ' + str(child['error']) if child.get('error') else ''}", highlight=False)
                if child.get("output"):
                    console.print("    " + child["output"][-600:].strip().replace("\n", "\n    "), highlight=False, markup=False)
    for event in shown.get("events") or []:
        line = " ".join(f"{k}={v}" for k, v in event.items() if k not in ("time", "event") and v not in (None, "", [], {}))
        console.print(f"  {event.get('time', '')[11:19]} {event.get('event')} {line[:200]}", highlight=False, markup=False)
    if shown["state"] not in ("running", "complete"):
        console.print(f"To fix the agent from it: prometheus bundle {slug} {run_id}, then /agent-improve <file> where it is developed.",
                      highlight=False)


@app.command(short_help="pack a failed run for /agent-improve where the agent is developed", rich_help_panel="Releases")
def bundle(
    slug: Annotated[str, typer.Argument(help="the agent")],
    run_id: Annotated[str, typer.Argument(help="the released run (its id, as `prometheus runs` lists it)")],
    with_data: Annotated[bool, typer.Option("--with-data", help="add the run as it is: the children's sessions and output, "
                                                             "its inputs, outputs and work (the caller's data)")] = False,
    out: Annotated[Path, typer.Option(help="folder to write the bundle in")] = Path("."),
):
    """Pack a released run's evidence into one file, to fix the agent where it is developed (/agent-improve <bundle>)."""
    made = run_script("run_bundle.py", "make", "--run", str(find_released_run(slug, run_id)), "--out", str(out),
                      *(["--with-data"] if with_data else []))
    if not made.get("ok"):
        raise PrometheusError(made.get("error") or "run_bundle.py failed")
    console.print(f"{made['bundle']} ({made['size_mb']} MB, {made['files']} files"
                  f"{', with the run' + chr(39) + 's data' if with_data else ''})", highlight=False)
    if made.get("redacted"):
        console.print(f"Replaced the values of {', '.join(made['redacted'])} with ${{NAME}}.", highlight=False)
    for warning in made.get("warnings") or []:
        console.print(f"[yellow]warning[/]: {warning}: check before you send it", highlight=False)
    if made.get("to_check"):
        console.print("Without the run's data, but these texts are in it and may quote it: check them before you send it",
                      highlight=False)
        for text in made["to_check"]:
            console.print(f"  {text[:300]}", highlight=False, markup=False)


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
