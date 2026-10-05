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
- [ ] 11. per-agent network allow-list (outbound proxy) and a model-key proxy: need your infrastructure choices

# Podman backend

- [x] 12. `SANDBOX=podman`: rootless container per worker, rendered from bwrap's arguments (`podmanArgs`); root folder of host mounts at the same paths; keep-id, no capabilities, no-new-privileges, pids limit, memory/CPU where cgroups allow, own loopback; `PODMAN_RUNTIME=runsc` for gVisor; orphan cleanup
- [x] 13. escape tests run against each installed backend (`backend` fixture); docs (ui/README, design), CLAUDE.md
- [x] 14. verified: podman 4.9.3 passes the escape tests (planting code, git config, worker start); gVisor (Ubuntu runsc 2023) cannot start rootless under WSL: router now passes ignore-cgroups and refuses to start if the runtime fails; install a current runsc from gvisor.dev to retry
- [ ] 15. outbound allow-list per agent (podman: an internal network + proxy) and a model-key proxy

# Outbound allow-list per agent (SANDBOX=podman)

- [x] 16. `ui/egress_proxy.py` (stdlib): CONNECT-only proxy; each agent has its own token; allows the hosts in its allow file; logs what it refuses
- [x] 17. podman: an internal network for workers (no route out), the proxy on it and on an outbound network; workers get `HTTPS_PROXY` + `NODE_USE_ENV_PROXY=1`
- [x] 18. what's allowed: base hosts (model providers, PyPI; `EGRESS_BASE` overrides) + hosts you approved for that agent, kept outside every sandbox (`<own>/network/<slug>.json`); the plan's `network:` lists what the agent asks for
- [x] 19. router `GET/POST /a/<slug>/network`; the web app's Settings shows asked-for and refused hosts, with Allow / Remove
- [x] 20. tests: the proxy (tokens, HTTPS only, refusals recorded, subdomains), from inside a podman sandbox (direct closed, refused host, allowed host), the routes; docs (ui/README, design, plan-schema, SKILL.md, troubleshooting, contributing), CLAUDE.md
- [ ] 21. later: a model-key proxy (the agent never holds the key)
