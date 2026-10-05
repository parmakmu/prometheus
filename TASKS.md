# Tasks (2026-10-05, security: sandbox escapes)

Status: [ ] todo · [~] doing · [x] done · [-] dropped (reason)

- [x] 1. `~/.pi/agent` read-only in sandboxes (extensions, npm, settings, trust: code that runs unsandboxed); writable only what Pi and pi-subagents must write
- [x] 2. router runs git and scripts on agent folders without a sandbox (repo config runs commands) → in the sandbox
- [x] 3. agent's own code read-only in its sandbox: `.pi/` (extensions, skills, settings, mcp) except what runs write, `.git/config`, `.git/hooks`
- [x] 4. `TRUST_USER_HEADER`: sandboxes reach the router on loopback and can name any user → a proxy secret
- [x] 5. uploads write through symlinks an agent left (dangling ones too) → refuse
- [x] 6. escape tests in tests/test_router.py (planting code, git config, upload links, proxy secret) + a worker starting in the hardened sandbox; the git attack confirmed to work unsandboxed; docs (design, ui/README, troubleshooting), CLAUDE.md; 285 tests pass

# Security, next layer

- [x] 7. secrets out of pi's environment: `.env` no longer loaded by the agent extension; the batcher adds the secrets a program's command or gate names (`secrets_for`, `batch_tool` `secrets`/`env_file`); validator: a secret only in a program's `run:` or gate
- [x] 8. `reader` subagent (no shell) in the template; validator warns on judgment over outside content with a shell; examples use it
- [x] 9. gate commands quote param values (as `run:`)
- [x] 10. docs (params-and-secrets, design, plan-schema, SKILL.md, troubleshooting), CLAUDE.md; 290 tests pass
- [-] 11. per-agent network allow-list and a model-key proxy: split (allow-list done, 16–20; key proxy is 21)

# Podman backend

- [x] 12. `SANDBOX=podman`: rootless container per worker, rendered from bwrap's arguments (`podmanArgs`); root folder of host mounts at the same paths; keep-id, no capabilities, no-new-privileges, pids limit, memory/CPU where cgroups allow, own loopback; `PODMAN_RUNTIME=runsc` for gVisor; orphan cleanup
- [x] 13. escape tests run against each installed backend (`backend` fixture); docs (ui/README, design), CLAUDE.md
- [x] 14. verified: podman 4.9.3 passes the escape tests (planting code, git config, worker start); gVisor (Ubuntu runsc 2023) cannot start rootless under WSL: router now passes ignore-cgroups and refuses to start if the runtime fails; install a current runsc from gvisor.dev to retry
- [-] 15. outbound allow-list and a model-key proxy: split (allow-list done, 16–20; key proxy is 21)

# Outbound allow-list per agent (SANDBOX=podman)

- [x] 16. `ui/egress_proxy.py` (stdlib): CONNECT-only proxy; each agent has its own token; allows the hosts in its allow file; logs what it refuses
- [x] 17. podman: an internal network for workers (no route out), the proxy on it and on an outbound network; workers get `HTTPS_PROXY` + `NODE_USE_ENV_PROXY=1`
- [x] 18. what's allowed: base hosts (model providers, PyPI; `EGRESS_BASE` overrides) + hosts you approved for that agent, kept outside every sandbox (`<own>/network/<slug>.json`); the plan's `network:` lists what the agent asks for
- [x] 19. router `GET/POST /a/<slug>/network`; the web app's Settings shows asked-for and refused hosts, with Allow / Remove
- [x] 20. tests: the proxy (tokens, HTTPS only, refusals recorded, subdomains), from inside a podman sandbox (direct closed, refused host, allowed host), the routes; docs (ui/README, design, plan-schema, SKILL.md, troubleshooting, contributing), CLAUDE.md
- [ ] 21. later: a model-key proxy (the agent never holds the key)

# Run API: an agent called on its own (2026-10-05)

- [x] 22. per-run values: `run_log.py new --params/--approve/--max-cost-usd`; `agent_params.load` puts the active run's `given_params` over `.env` (a relative path resolves inside the run); compile counts `preapproved`
- [x] 23. `plan_edit.py card`: what a caller sees (params without values, outputs, approvals, reviews)
- [x] 24. `ui/api.ts`: `/a/<slug>/api` (card, runs, events, answer, outputs, cancel); one call at a time per agent, a queue; a session of its own per call ("API · …"); questions wait in `needs_input` for the caller; calls and keys kept outside sandboxes
- [x] 25. API keys per agent (`/a/<slug>/api-keys`, hashed); a key opens that agent's API only; router tests
- [x] 26. Prometheus `uploads/`; tabs → spaces + `.editorconfig`; "Conversations" → "Sessions" in the web app
- [x] 27. found live: every sandboxed worker had lost its Pi packages (pi-subagents, so `/agent-auto`) since Pi state went read-only (`trust.json.lock`): the sandbox's `~/.pi/agent` is now its own tmpfs with each entry mounted in; regression test
- [x] 28. live call on emails-to-excel through a key (run 2026-10-05-5): 3 files sent, params over .env, done in 64 s, $0.0013 children, workbook downloaded; cancel verified (runs -3, -4 stopped)
- [x] 29. web app: API keys in the agent's Settings; docs (ui/README "The run API", troubleshooting, commands)
- [ ] 30. caller side: an agent calling another (`calls:` in its plan, a key handed to its sandbox, answering its questions)
- [x] 31. refresh removes what an older prometheus left under the old name (`.pi/extensions/workflow`, `.pi/skills/workflow`: Pi still loaded them); emails-to-excel and count-words-csv cleaned
- [x] 32. a call's cost includes the model that drove it (`cost: {agent, subagents}`)
- [x] 33. the allow-list test failure: not reproduced in 3 full runs (918 tests); its assertion prints what the sandbox saw, so a next failure names the cause

# Worker tests (2026-10-05)

- [x] 34. `tests/test_worker.py`: a real worker in each sandbox through the router, with a probe extension in the test user's Pi state (no model): your packages and the agent's commands loaded, pi-subagents' RPC answers, a command's output reaches the page, a dialog becomes a question the page answers; fails on the read-only Pi state that broke packages; 312 tests pass
- [ ] 35. live: emails-to-excel with an approval, in the browser and through the API (the answer path)
- [ ] 36. agent-to-agent calls (30)
- [x] 37. live planning in the browser (`/agent-new --from`, emails-to-excel-approval): $0.02, a real plan critic child (first live), one question; found: during first planning the critique is written by hand (only an agent has `agent_record_critique`), so no `plan_digest` and the ready guard has a hole: proposal pending
- [x] 38. the send button blinked after a run: syncs started on a status polled before the run ended (still "busy"), again until the next poll; now a status counts only when asked after our run ended
- [x] 39. live API call with an approval (no `approve`): needs_input with the approval question, answered through the API: works. Found: /agent-auto's hold outlived its hand-over (a follow-up into a running notice turn starts no turn), so the model, refused agent_compile after the approval, stopped; the hand-over now releases the hold; test drives a wave that runs meanwhile (harness: background commands, FAKE_SUBAGENTS_SLOW), fails on the old code
- [x] 40. the critic's record, checked for new agents too: prometheus has `agent_record_critique` (slug); `plan_digest` written before validating; a plan that needs the critic is ready only with a matching digest (no more pass for a hand-written critique); examples stamped (a test keeps them current); SKILL.md, critic-contract, commands, troubleshooting
- [x] 41. one-time stamp, trusted as reviewed: emails-to-excel (main), emails-to-excel-approval (main and its run branch); count-words-csv needs no critic
- [x] 40. a plan that needs the critic is ready only with a matching `plan_digest`, written only by `agent_record_critique`, now in prometheus too (first planning); SKILL.md, critic-contract, commands, troubleshooting; examples stamped (a test keeps them in step); the three agents stamped once (trusted as reviewed, committed); 315 tests pass
- [x] 42. plan.yaml is for people, in sections under `# ── ` banners; `.plan/` holds unknowns, questions, critique, agents (a file each; `utils.read_plan`/`write_plan` keep one plan in memory, digest unchanged); critic reads `.plan/` too; `.plan/critique.yaml` writable only by agent_record_critique (both extensions); a section left in plan.yaml is an error; refresh moves an older plan's sections; examples are folders; web workspace shows `.plan`; count-words-csv and emails-to-excel refreshed; 319 tests pass
