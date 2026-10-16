# Tasks

Status: [ ] todo · [~] doing · [x] done · [-] dropped (reason)

Where this is going (2026-10-08): install with `uv tool`, planner in `~/.prometheus`, agents in `PROMETHEUS_AGENTS`;
three modes (interactive, embedded via `load_agent`, service through `prometheus serve`); embedded and service run a
frozen release as code, with no orchestrator. GitLab; users are developers; everyone uses their own model keys.

## Open

- [ ] 75. the run protocol (~3k tokens) is read at the start of every run, even one that turns out fully reused
  (email-summaries run 2026-10-17-2: the read was most of the run's cost). Give each start only what it needs: the
  tool results already carry `next` and conclusions; the protocol could be split so a plain run reads a short core
- [ ] 73. a map's merge checks that every item of the split appears once in the result (the batch task already says
  to name items as the batch file does)
- [ ] 38. per-item reuse in map stages: a batch child writes one result per item (its id), a store keyed by item
  content + stage fingerprint, split leaves known items out, merge restores item order. Until an agent runs over a
  growing input; 68 reuses a map stage whole
- [ ] 28. live: emails-to-excel with an approval in the browser

## Done (details in git history and `docs/design.md`)

- [x] 1–37. install and launch, `prometheus` CLI, releases, frozen runner and `load_agent`, sharing with plain git,
  `prometheus serve`, A2A (card, tasks, pauses, `pattern: a2a`)
- [x] 25–26, 39–51. released runs reuse each other; `prune`; first live map stage; stage `instructions`; plan field
  names; review fixes; `.compiled/<wave>.{js,json}`; Pi 1.3 + pi-subagents 0.78; packaging + psutil; a run works on
  its own branch; "resolve"; consistent field names
- [x] 46. live: /agent-auto on Pi 1.3 + pi-subagents 0.78 (emails-to-excel run 2026-10-16-1)
- [x] 52–59. bundles: `prometheus bundle`, `serve` `GET /runs/<id>/bundle`, `run.bundle()`, `/agent-bundle`;
  `/agent-improve <bundle>`; `prometheus run <slug>@<ref> --bundle`; `prune --keep-failed`
- [x] 60–64. releases: no `.pi/extensions/` in an unpacked release; `RELEASE.json`; `prometheus runs`;
  `prometheus doctor <slug>`; `prometheus install <source>@v1` (frozen, releases folder `releases/<slug>/<version>/`)
- [x] 65. `docs/python-api.md`
- [x] 66–67. children's sessions and records (`work/.pi/sessions/`) bundled; `agent_sessions` reads transcripts;
  `prune` removes empty launch folders
- [x] 68–72. a map stage reused whole; outputs anywhere in the run reused; a fully reused wave recorded at compile
  (`launched: false`); deliverables with item counts in the finished result
- [x] 74. results state conclusions: a finished run's `report` (`do`: open nothing when every deliverable was
  checked); a new run starts without asking whether inputs changed. Live: run 2026-10-17-2, 5 tool calls in 11 s

## Dropped

- [-] 2–3. devpod checks · 17. release hashes · 21–24. Dockerfile, CI, devpod image (the service team's) · 27.
  `calls:` (A2A instead) · 29. chat web app features (frozen) · 30. model-key proxy (own keys)
