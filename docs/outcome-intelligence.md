# Outcome Intelligence v1.2.0

## Engine seam

`RouterEngine` exposes `begin_task`, `plan_route`, `observe_event`, `finalize_task`, `evaluate_policy`, and `status`. Task ledger, risk classifier, route selector, outcome intelligence, and evidence storage stay behind this seam.

## Lifecycle contract

The implementation follows the [official OpenAI Hooks documentation](https://learn.chatgpt.com/docs/hooks): it consumes event JSON from stdin, keys turns by `turn_id`, correlates tools by `tool_use_id`, observes `Agent`/`spawn_agent` plus subagent lifecycle events, and treats `SessionEnd` as root-session-only cleanup. It never reads `transcript_path`; the official documentation identifies transcript format as unstable.

`UserPromptSubmit` creates one task per user turn. Hook and MCP processes share the canonical `CODEX_HOME/codex-adaptive-router` root unless `CODEX_ADAPTIVE_ROUTER_DATA` explicitly overrides it. `PLUGIN_DATA` remains a read-only legacy source: an MCP-only `task_ref` can import its matching validated v2 task events from any exact `CODEX_HOME/plugins/data/codex-adaptive-router-*` root. Route IDs are preserved, event IDs and dedupe keys remain idempotent, and conflicting legacy route IDs fail closed.

`route_plan(task_ref=...)` returns the existing route. Asynchronous events are deduplicated and correlated by turn; append-only route events rebuild a missing ledger after a partial write. `Stop` records provisional evidence until objective verification or explicit user correction supplies quality.

## Privacy and learning gates

The salt is local-only and generated with restrictive permissions. Persisted evidence is limited to enums, bands, HMACs, IDs, timestamps, counts, and bounded route transitions.

Quality and risk gates precede resource minimization. The incumbent wins evidence ties, and an unexecuted cheaper candidate remains inconclusive. Policy proposals are axis-specific and always require human confirmation.

## Capability-budget attribution

New evidence uses event schema v3; existing v1/v2 evidence is read-only compatible and is never rewritten. Outcomes may identify a route stage and independently classify model fit, effort fit, context fit, tool-data fit, and a bounded result signal. A supplied stage must exist in the task's stored plan; a sole required stage is inferred, otherwise attribution stays unknown.

- A replacement that keeps role and model fixed and changes only effort may support `reasoning_budget` evidence.
- A replacement that keeps role and effort fixed and changes only model may support `model_capability` evidence.
- Multiple changed axes are `confounded`.
- Deficient context or tool data takes precedence and cannot create model- or effort-axis evidence.
- Model proposals hold role and effort fixed; effort proposals hold role and model fixed. Below-floor downgrades are invalid.

An `exceptional_positive` result signal stores no metric or result text. If the immutable route lacks audit, the outcome includes an idempotent required auditor/Sol/XHigh follow-up stage. The original route remains unchanged.

Metrics normalize mixed v2/v3 history before aggregation. They include task-class/model/effort success, model and effort fit counts plus under/over rates with per-axis known-fit denominators, floor violations, decision leakage, mechanical Sol share, objectively verified adjacent-stage handoff success, quality-adjusted resource bands, and model-effort comparable counts. All dimensions remain bounded classifications or counts; no raw task content is retained.

Evolution sync emits new batches, manifests, metrics, policies, schemas, and `latest.json` with explicit LF bytes. Previously published CRLF immutable artifacts remain byte-for-byte untouched and continue to participate in their original hash chain.
