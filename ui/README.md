# prometheus web UI (v0)

A browser app for prometheus and its workflows, for one person or a team.

- `router.ts` is the server the browser talks to: it checks who is calling, finds their folder, and forwards to a worker, starting it on first use and stopping it after 15 idle minutes (never while it works).
- `worker.ts` is one agent: a Pi SDK session in one folder (prometheus, or one workflow), served as an [AG-UI](https://docs.ag-ui.com) endpoint on a unix socket only the router reaches. One process per user and agent, so each has its own session and `.env`.
- `web/` is an [assistant-ui](https://www.assistant-ui.com/docs/runtimes/ag-ui) app (Next.js) that talks to the router.

From the repository root (one `package.json` for the whole repo):

```sh
npm install
npm run ui:server     # the router, http://127.0.0.1:8787   (DEBUG=1 logs every Pi event)
npm run ui:web        # in a second terminal
```

Open the link `ui:server` prints, `http://localhost:3000/?token=…`. The page keeps the token for that tab and sends it with every request; without it the server answers 403. A restart makes a new token, so open the new link (or set `PROMETHEUS_TOKEN` to keep one).

**Who can call the server:** only the web app's origin (`UI_ORIGIN`, default `http://localhost:3000`), only at a loopback host name (`localhost`, `127.0.0.1`), and only with the token. So a page on another site can't drive the agent from your browser (origin), nor can a site re-pointed at 127.0.0.1 (host), and another program on the machine doesn't know the token. There are no logins yet: this is for one person on their own machine.

If the page says it can't reach the agent server while `ui:server` is running, and your browser runs outside WSL, start it with `HOST=0.0.0.0 npm run ui:server`: the server listens only on `127.0.0.1` by default, because it has no authentication.

Needs Node 26 or later (it runs `router.ts` and `worker.ts` directly) and the same Pi setup as the terminal: your model, your saved trust for this folder, and the globally installed packages (pi-subagents, ask-user-question).

The page has three columns:

- **Left: workflows and conversations.** Each workflow with its state in plain words and a dot when it waits for you (from `GET /workflows`, i.e. `list_workflows.py`, not the model; refreshed every 15 s), then your conversations, titled by their first message.
- **Middle: the chat.** Prometheus's questions appear as a card with each option's description and the recommended one marked. Its tools leave a one-line chip that points to the right.
- **Right: the canvas,** where the agent shows things: the workflow it created, the plan with its steps, the critic's verdict. Selecting a workflow on the left shows its details and runs there. With nothing selected, it shows what waits for you and the recent runs of all workflows, with their cost. Tabs switch between them; something new from the agent takes the focus.

**One person or a team:**

| Setting | Effect |
|---|---|
| (none) | One person: you are `DEV_USER` (your login name), your folder is this repository, and you need the token. |
| `USERS_ROOT=/data/users` | Each user's folder is `USERS_ROOT/<user>/prometheus`, their own copy (workflows inside it). A user without one gets 403. |
| `TRUST_USER_HEADER=x-forwarded-user` | The user comes from that header, set by a login proxy in front (e.g. oauth2-proxy with your team's identity provider); no token then. Keep the router on loopback so only the proxy reaches it, and set `UI_ORIGIN` and `ALLOWED_HOSTS` to the public address. |
| `IDLE_MINUTES` | When idle workers stop (default 15). |

**Adding a user:** `USERS_ROOT=/data/users ui/add-user.sh alice` creates `alice/{prometheus, pi-agent, files, home}`: her own clone of this repository (with its `.venv`), her Pi state with your settings and model key, a folder for her input files, and a home. `--update` pulls this repository's latest commit into her copy.

**Sandboxes** (bubblewrap, on by default when `bwrap` is installed): every worker runs in its own, and sees only what it needs, at the same paths as outside.

| | prometheus (planner) | a workflow |
|---|---|---|
| prometheus's code | read-only | not there |
| `workflows/` | read-write (creates, refreshes), every `.env` reads as empty | only its own folder, read-write, with its `.env` |
| your Pi state, input files (`files/` or `SANDBOX_READ`) | sessions and settings read-write; files read-only | the same |
| the rest of your home, other users | not there | not there |

The network is open (the model, a workflow's connectors). With a systemd user session, each worker also gets `MEMORY_MAX` (4G) and `CPU_QUOTA` (200%); WSL usually has none. `SANDBOX=off` turns sandboxes off (development only). To see what a sandbox can do: `node ui/router.ts sandbox-run <user> p -- <command>` (or `w/<slug>` for a workflow).

Routes: `GET /me`, `GET /workflows` (your workflows, from `list_workflows.py`), `/p/…` (prometheus's agent: `agent`, `status`, `commands`) and `/w/<slug>/…` (that workflow's).

How it maps:

- Pi's text, thinking and tool calls stream as AG-UI text, reasoning and tool-call events.
- An extension dialog (`ask_user_question`, select, confirm, input) ends the run with an AG-UI interrupt. The page shows it above the composer, and your answer resumes the run.
- **Panels:** the worker tells extensions it is a terminal (`mode: "tui"`), so commands with a panel of their own (`/ccstyle`, `/goal…`, `/subagents…`) open it: `ctx.ui.custom()` gets the extension's component with a minimal stand-in for Pi's TUI (its size, `requestRender`), and each frame is the component's own `render(width)`, sent whole (as [pi-web](https://github.com/agegr/pi-web) does); the chat shows it as a live card. pi-tui's standard widgets (`SettingsList`, `SelectList`, `Input`) are hijacked in the worker (their `render` records them), so a frame also carries their state and the card draws native controls (switches, segmented buttons, dropdowns, option buttons, text fields) that act through the widgets' own methods (`/…/panel/<id>/action`); other components show as styled text, (a `pi_panel` tool call; `/…/panel/<id>/stream`, `input`, `resize`) that takes keys until the extension closes it. `ask_user_question` is the exception: its panel is declined, so it falls back to its dialogs, which the chat shows as a question card. Pi's own built-in commands belong to its interactive app, not to extensions, so the worker does the useful ones itself with the SDK, and the page offers them as buttons too: `/model` (a searchable picker on the canvas; also the model name in the status bar), `/thinking` (also the status bar's dropdown), `/compact [instructions]` (also the Compact button by the context meter), `/session`, `/name` and `/reload` (also the ⋯ menu over the chat); `/new` and `/resume` point to the Conversations list. Changes apply to the conversation only, not to Pi's defaults. Credentials and machine settings (`/login`, `/settings`, …) stay with the terminal.
- Extension messages (such as `/workflow-list` output) and notifications appear as assistant text.
- **History and background work.** A run that brings no new message (none, or only ones the server sent) is a *sync*: the worker answers with the conversation so far as an AG-UI messages snapshot, from Pi's own session; if the agent is working on its own (a wave finished, say), it adds what the reply in progress says so far and streams the rest until the agent settles. The page syncs when it opens, and whenever the status bar sees the agent busy without a run of its own. A page that goes away mid-run (reload, closed tab) doesn't stop the agent; the next sync picks it up.
- A worker continues the folder's latest Pi session (as `pi --continue`), so a restart (idle stop, router restart) keeps the conversation. That is also the session the terminal's `pi --continue` would pick.

Known limits: the model key in each user's `pi-agent` is readable by their agents; the network inside sandboxes is not restricted; all users' workers run as the OS user that runs the router (one Linux user per person would add a second wall). **Switching agents:** the left column lists prometheus and your workflows; clicking one points the chat at that agent (`/p` or `/w/<slug>`), which loads its history, its status and its commands, and shows its state in the chat's header. The choice is kept in the address (`#/w/emails-to-excel`), so a reload stays on it. **Conversations** are the agent's Pi sessions, kept in its folder (`.pi/sessions`, the `sessionDir` of its `.pi/settings.json`), the same ones `pi --resume` lists there. Each runs in a worker of its own (`/…/s/<id>/…`; `new-…` starts one; no id is the latest); the list on the left is `GET /…/sessions`, newest first, and `DELETE /…/sessions/<id>` removes one for good (refused while its agent works). The planner's sandbox sees its own `.pi/sessions` read-write and every workflow's as empty.

**Files:** each user has a files folder (`USERS_ROOT/<user>/files`; for one person `~/.prometheus-sandbox/files`), mounted read-only in every sandbox. The *Files* entry on the left opens it on the canvas: browse, upload files or a whole folder, delete, and copy a folder's path to give a run that asks for one. A workflow's details on the canvas list its results (`runs/*/outputs/`) to download. Routes: `GET /files?dir=`, `PUT /files?path=` (the body is the file, at most `MAX_UPLOAD_MB`, 200), `DELETE /files?path=`, `GET /files/download?path=`, `GET /w/<slug>/outputs`, `GET /w/<slug>/download?path=runs/<id>/outputs/…`. Paths are checked to stay inside, symlinks included.

**Terminal:** the *Terminal* button over the chat opens `pi` itself on the canvas, in a real terminal in that agent's sandbox: for what only the terminal does (`/ccstyle`, `/model`, panels). It is its own Pi session, not the chat's. `pty_relay.py` (standard library, so no native module to build) runs it in a pseudo-terminal; output reaches the page as an event stream (`/…/term/stream`), keys and resizes as POSTs (`/…/term/input`, `/…/term/resize`). A page that reconnects sees the last 256 kB; pi stops a minute after the last page closes.

In the history, a command's prompt (what `/workflow-new` sends the model) shows as a user message.
