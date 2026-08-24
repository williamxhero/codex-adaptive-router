# Codex Adaptive Router

`codex-adaptive-router` is a local Codex plugin that routes non-trivial work to the appropriate GPT-5.6 model and reasoning effort, while keeping the primary thread responsible for intent and final integration.

It is designed for all projects, with a generic profile by default and a stricter `quant` profile for quantitative research and backtesting.

## What it does

| Work state | Route |
| --- | --- |
| Tiny, bounded task | Primary thread directly |
| Code search, logs, references, call chains | `router_code_mapper` — Luna Medium |
| Defined tests, scans, sweeps, benchmarks, metrics | `router_experiment_runner` — Luna Medium |
| Frozen specification, complex implementation | `router_research_engineer` — Terra High |
| Diagnosis, research, causal/statistical judgment | `router_researcher` — Sol High |
| Durable architecture or domain semantics | `router_architect` — Sol High |
| Adversarial review or anomalously strong result | `router_adversarial_auditor` — Sol XHigh |
| Valuable open exploration or local optimum escape | `router_strategy_scout` — Sol XHigh |

Luna and Terra generate bounded evidence or implementation. They never own unresolved research, architecture, statistical, market-semantic, or irreversible decisions.

## Important capability boundary

The plugin automatically routes **subagents**. A normal Codex Hook cannot change the model already selected for the active primary thread. The intended global default is therefore Sol Medium for the primary thread, with automatic specialist routing for the parts that need a different capability or reasoning depth.

True pre-thread primary-model selection needs a separate Codex App Server/SDK launcher; it is intentionally not hidden inside this plugin.

## Layout

```text
codex-adaptive-router/
├── .codex-plugin/plugin.json       Plugin metadata
├── .mcp.json                       Local stdio MCP registration
├── hooks/hooks.json                Context injection and privacy-bounded lifecycle events
├── profiles/                       generic and quant routing policies
├── skills/                         Runtime, maintenance, and install workflows
├── templates/agents/               Namespaced global custom-agent definitions
├── scripts/router_core.py          Deterministic routing and learning engine
├── scripts/router_mcp.py           MCP server
├── scripts/router_hook.py          Hook adapter
├── scripts/install_user_layer.py   Backup-first global-agent/config installer
├── scripts/uninstall_user_layer.py Namespaced-agent remover
├── tests/                          Standard-library regression tests
└── docs/                           Detailed architecture and Gardener bridge
```

## Runtime flow

```text
User task
  -> UserPromptSubmit Hook adds a compact route hint
  -> Primary thread calls adaptive_router.route_plan for non-trivial work
  -> Primary thread works directly or spawns the selected specialist
  -> Verified result / correction / escalation records outcome evidence
  -> Repeated independent evidence creates a proposal
  -> Shadow evaluation observes the proposal without enforcing it
  -> Explicit user confirmation activates the new versioned policy
```

The hooks fail open: a routing-storage or classification issue never blocks ordinary Codex work.

## Learning safeguards

The local event log stores only hashed task/session/project identifiers plus route and outcome metadata. It never stores raw prompts, source paths, tool output, code, logs, or credentials.

A learned policy cannot activate from one task. It must pass all of these gates:

1. At least three independent sessions with mean outcome confidence of at least `0.85`.
2. At least two distinct project fingerprints before a proposal is considered global.
3. Shadow evaluation: the proposal is advisory and the old policy remains active.
4. Five successful shadow observations; two failures reject it.
5. An explicit `confirmed_by_user: true` tool call.

The active policy is versioned at `PLUGIN_DATA/policy/current.json` when Codex supplies `PLUGIN_DATA`; otherwise it uses `~/.codex/codex-adaptive-router/`.

## Codex-Gardener integration

Adaptive Router and Codex-Gardener remain independent plugins.

- Router owns machine-readable route telemetry and the executable policy.
- Gardener owns cross-session curation, conflict checks, scope selection, and promotion review.
- `adaptive_router.router_policy_status` emits `gardener_candidates` without raw prompts or paths.
- A dedicated Router maintenance task may pass those candidate fields into Gardener's ordinary curation process.
- Neither plugin relies on Hook ordering or reaches into the other plugin's cache directory.

See [Gardener bridge](docs/gardener-bridge.md) for the handoff contract.

## Install after review

This repository directory is the source package; it has not modified any user-level Codex files.

1. Add this plugin to a personal or repository marketplace and install it in Codex.
2. Review and trust its Hooks in Codex.
3. If you want its custom agents available in every project, run:

   ```powershell
   python scripts\install_user_layer.py
   ```

4. Add `--set-root-model` only if every newly created primary thread should default to `gpt-5.6` / Medium:

   ```powershell
   python scripts\install_user_layer.py --set-root-model
   ```

The installer makes timestamped backups of existing `config.toml` and `router_*.toml` agent files before replacing them. It does not touch any project configuration.

## Validate locally

```powershell
python scripts\validate_router.py
python -m unittest discover -s tests -v
```

## Current limitations

- Profile inference is deterministic keyword/risk classification. The primary thread can correct it after inspecting the task.
- Cost/quality outcomes are intentionally evidence-driven; token usage is not assumed to be available from every Codex Hook surface.
- The Router does not automatically use Max or Ultra.
- An active thread's primary model remains unchanged; use a future launcher for true pre-thread model selection.
