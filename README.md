# Codex Adaptive Router

`codex-adaptive-router` 1.2.0 adds Capability–Budget Separation. Model capability (`Luna < Terra < Sol`) is independent from reasoning effort, every authority has a hard capability floor, and Route Plan v2 stages bounded specialist work while the Sol primary thread retains final intent, integration, and conclusions.

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

Reasoning effort never compensates for a model below its floor. Evidence requires at least Luna, implementation requires at least Terra, and decision or audit authority requires Sol. Max and Ultra are never automatic; they require an explicit user constraint or a human-confirmed policy override.

Route Plan v2 uses deterministic templates: tiny direct work stays Root Sol Medium; discovery collects with Luna then returns to the Root; implementation uses Root Sol Medium for intent/integration around Terra implementation and Luna verification; research and architecture delegate High-budget decision stages to named Sol specialists. `direct` always means the current Root at Sol Medium. High-impact plans use an auditor specialist, and an `exceptional_positive` outcome adds an idempotent required Sol XHigh audit follow-up when the immutable original plan lacked one.

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

## Outcome Intelligence loop

```text
User task
  -> UserPromptSubmit creates task_ref + initial route
  -> route_plan(task_ref=...) confirms it without rerouting
  -> tool and subagent hooks uniquely associate bounded agent lifecycles to planned stages
  -> Stop creates a provisional outcome; later verification/correction enriches it
  -> objective, user-confirmed replacement evidence creates axis-specific proposals
  -> eligible tasks compare incumbent + candidate without enforcing the candidate
  -> Explicit user confirmation activates the new versioned policy
```

The hooks fail open: a routing-storage or classification issue never blocks ordinary Codex work. Codex 0.147 does not execute async hooks, so the lifecycle/evidence-critical `PostToolUse`, `SubagentStop`, and `Stop` hooks run synchronously with a three-second timeout per hook; `router_hook.py` still returns a fail-open response on handled errors.

### v1.1.1 task-ref/store consistency hotfix

Hooks and the MCP server now write to one canonical runtime root: `CODEX_ADAPTIVE_ROUTER_DATA` when explicitly set, otherwise `CODEX_HOME/codex-adaptive-router` (or `~/.codex/codex-adaptive-router`). `PLUGIN_DATA` is legacy-read-only and is never the primary write root. When an MCP-only `task_ref` exists in an older `CODEX_HOME/plugins/data/codex-adaptive-router-*` store, the Router imports only that task's validated, privacy-bounded v2 events into the canonical store, preserving its route ID and deduplicating by event ID and dedupe key. Conflicting legacy route IDs fail closed.

### v1.2.0 Capability–Budget Separation

Profiles use schema v3 with explicit authority, capability floor, legal models, independent effort bands, and Sol escalation conditions. Decision Features v2 accepts any documented subset and deterministically fills the remainder. Outcome Intelligence v3 separates model, effort, context, tool-data, and execution failure axes; multi-axis replacements are confounded rather than misattributed. Agent lifecycle hooks associate only a unique role/model/effort match to an unfinished required stage using HMAC identity; stop records completion but never objective verification. Stage completion is validated against the stored plan, adjacent objectively verified stages drive handoff metrics, and model/effort under/over rates use independent known-fit denominators.

See [Capability–Budget ADR](docs/adr/0001-capability-budget-separation.md) and [Outcome Intelligence](docs/outcome-intelligence.md).

## Learning safeguards

The local event log uses a private local salt and HMAC identities. It stores append-only event IDs/sequences plus structured classifications and aggregates. It never stores or uploads raw prompts, paths, code, tool input/output, assistant messages, transcripts, credentials, or secrets. Hooks fail open; GitHub sync and privacy validation fail closed.

A learned policy cannot activate from one task. It must pass all of these gates:

1. At least five same-direction, objective, user-confirmed replacement outcomes across three sessions, mean confidence `>= 0.85`, and no verified high-risk regression.
2. At least two distinct project fingerprints before a proposal is global.
3. Role, model, and effort proposals are independent axes.
4. Shadow readiness requires 10 comparable observations, 8 candidate wins, at most 1 loss, and no high-risk regression. An unexecuted downgrade is inconclusive.
5. An explicit `confirmed_by_user: true` tool call.

Contextual bandits and automatically learned classifier weights are prohibited before a later design review with 50-100 high-quality outcomes. v1 evidence stays read-only.

## GitHub evolution governance

The sync writes immutable `batches/`, hash-chained `manifests/`, versioned `policies/revision-N.json`, and recomputable `metrics/revision-N.json`; `latest.json` is the only mutable pointer. Root-level v1 artifacts are marked legacy. CI rejects schema, privacy, secret/path, hash-chain, duplicate-ID, and append-only violations. A run without `--push` uses a temporary preview and leaves the dedicated clone clean.

The active policy is versioned under the canonical runtime root. `PLUGIN_DATA` is consulted only as a legacy source for task-level compatibility.

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

4. Add `--set-root-model` only if every newly created primary thread should default to `gpt-5.6-sol` / Medium:

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

- Decision Features v2 performs deterministic structured risk/cognitive classification; keywords are only a fallback and callers may provide any known subset.
- Cost/quality outcomes are intentionally evidence-driven; token usage is not assumed to be available from every Codex Hook surface.
- The Router does not automatically use Max or Ultra.
- An active thread's primary model remains unchanged; use a future launcher for true pre-thread model selection.
