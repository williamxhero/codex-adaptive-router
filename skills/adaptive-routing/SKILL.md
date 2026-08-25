---
name: adaptive-routing
description: Select and execute capability-safe, token-aware direct, subagent, or visible-task routes; preserve Thin Root ownership, depth-two recursive dispatch, and Sol-owned decisions.
---

# Adaptive Routing v1.3

Use this Skill for non-trivial work, multi-file changes, research, diagnosis, architecture, quantitative attribution, backtesting, or long-running/cross-project execution. Tiny safe tasks may remain direct.

## Route Plan v3

1. Honor explicit user constraints unless safety or a capability floor prevents them.
2. Confirm a hook-created `task_ref` idempotently with `route_plan`; never reroute an existing reference.
3. Freeze semantics before implementation. Capability and quality gates are evaluated before token cost.
4. Execute the selected `execution_target`: `direct`, `subagent`, or `visible_task`. Complex work defaults to `subagent`; long-running, cross-project, or context-isolated work uses `visible_task`.
5. If a required worker is unavailable, stop at the structured dispatch blocker. Root must not silently take over complex work.
6. Max and Ultra are never automatic. They require an explicit user constraint or a human-confirmed policy override.

## Dispatch and recursion

- Claim every delegated stage with `claim_stage` before execution.
- Root is depth 0, child is depth 1, grandchild is depth 2. Depth 2 may not delegate further.
- One parent has one active specialist by default. Up to three genuinely independent read-only children may run concurrently only when the plan declares the slot count and each claim supplies a distinct independence key.
- One logical repository has one writer lease across the dispatch tree.
- A child may sequentially freeze and reroute remaining work more than once. The immutable Route Plan is never rewritten.
- Only Root may create a visible task. Use `[AR][MODEL-EFFORT] short objective`; archive only after terminal success plus passed quality, boundary, scope, verification, and required-audit gates.

## Decision boundary

- `router_code_mapper` / Luna Medium: read-only code and evidence mapping.
- `router_experiment_runner` / Luna Medium: defined tests, scans, benchmarks, and metrics.
- `router_research_engineer` / Terra High: frozen-spec implementation and the only default writer.
- `router_researcher` / Sol High: diagnosis, research design, and causal judgment.
- `router_quant_researcher` / Sol High: quantitative attribution and statistical conclusions.
- `router_architect` / Sol High: durable architecture, time/accounting, and market semantics.
- `router_adversarial_auditor` / Sol XHigh: high-impact or unusually strong result review.
- `router_strategy_scout` / Sol XHigh: open-ended exploration or escape from a demonstrated local optimum.

Root retains intent, integration, acceptance, and the user-facing answer. Luna and Terra never resolve undefined semantics or own research/statistical/irreversible conclusions.

## Outcome Intelligence v4

Record outcomes only when meaningful evidence exists. Include the lease and observed role/model/effort/target when known. `SubagentStop` is lifecycle evidence, never objective verification. Exact local token counts are accepted only from stable Codex/provider usage or an exact caller report; otherwise leave them unavailable.

Learning uses the observed execution of the unique primary stage, requires adequate context/tool data plus matched boundaries/scope/verification and a completed delegated lease, isolates model and effort axes while holding the other route covariates fixed, rejects confounded evidence, runs non-enforcing shadow evaluation under the same gates, and still requires explicit human confirmation. GitHub projection contains only token bands/aggregates, HMAC identities, and enums—never raw prompts, paths, code, tool I/O, titles, or transcripts.

For high-uncertainty quantitative attribution, use Sol High framing, Luna evidence or Terra frozen implementation, Sol High synthesis, and Sol XHigh audit when impact, conflict, or exceptional results require it.
