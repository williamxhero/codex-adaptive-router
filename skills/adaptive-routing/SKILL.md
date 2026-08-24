---
name: adaptive-routing
description: Select and execute the appropriate Codex model, reasoning effort, and specialist route for non-trivial work; preserve a Sol-owned decision boundary for ambiguous, architectural, research, and high-impact conclusions.
---

# Adaptive Routing

Use this Skill whenever a request is non-trivial, spans multiple files or tools, needs a specialist, contains a high-impact decision, or asks for research, diagnosis, architecture, backtesting, or a large batch operation. Do not invoke it for a tiny safe task the primary thread can complete more cheaply than delegating.

## Route

1. If the user explicitly specifies a model, reasoning effort, agent, or asks for no delegation, honor that request unless a higher-priority safety rule prevents it.
2. `UserPromptSubmit` creates the task and initial route. Call `adaptive_router.route_plan` with its `task_ref` to confirm it idempotently; include structured `decision_features` and user `constraints` when known. Pass `task_state: "frozen"` only after semantics are settled.
3. Treat the returned route as a default. Correct it when direct evidence from the task makes another role safer, and state the reason briefly.
4. Spawn at most one specialist by default. Use parallel specialists only when their work is genuinely independent and the extra evidence or isolation is worth the token cost.
5. The primary thread owns the final intent, cross-agent integration, and all important conclusions.

## Decision boundary

- `router_code_mapper` / Luna Medium: code search, references, logs, call chains, and bounded evidence collection.
- `router_experiment_runner` / Luna Medium: already-defined tests, scans, benchmarks, parameter sweeps, and metric collection.
- `router_research_engineer` / Terra High: implementation after the specification is frozen.
- `router_researcher` / Sol High: ambiguous diagnosis, causal explanation, research design, and statistical judgment.
- `router_architect` / Sol High: durable architecture, domain semantics, timing, data availability, accounting, and compatibility decisions.
- `router_adversarial_auditor` / Sol XHigh: unusually good results, planned acceptance/deployment, leakage/overfit risk, and high-impact review.
- `router_strategy_scout` / Sol XHigh: genuinely open, high-value novel exploration or a demonstrated local optimum.

Luna and Terra may produce evidence and implementation, but they must not independently resolve undefined semantics, research conclusions, or irreversible architecture. Escalate those decisions to a Sol route.

Do not automatically use Max or Ultra. If XHigh is insufficient, explain why and ask the user whether additional cost is justified.

## Quant profile

When the task concerns strategies, backtests, market regimes, factor exposures, trading semantics, or statistical validity, set `profile: "quant"`.

- Do not interpret the highest Sharpe or best sweep row as strategy validity.
- Before accepting an important strategy result, consider out-of-sample behavior, regime dependence, parameter-neighborhood stability, costs, turnover, concentration, leakage, and multiple testing.
- Route time semantics, fills, T+1, limits, halts, rolling contracts, margins, and accounting to `router_architect`.

## Outcome evidence

After meaningful routed work, call `adaptive_router.record_route_outcome` only when there is real evidence: verification passed, user correction, material failure, escalation, or a deliberate model override. Do not manufacture outcomes.

For an escalation or override, provide the replacement role/model/effort. Proposal evidence also requires objective verification and explicit user confirmation. Role/model/effort proposals are independent axes; policy never changes automatically.
