# Outcome Intelligence v1.1.1

## Engine seam

`RouterEngine` exposes `begin_task`, `plan_route`, `observe_event`, `finalize_task`, `evaluate_policy`, and `status`. Task ledger, risk classifier, route selector, outcome intelligence, and evidence storage stay behind this seam.

## Lifecycle contract

The implementation follows the [official OpenAI Hooks documentation](https://learn.chatgpt.com/docs/hooks): it consumes event JSON from stdin, keys turns by `turn_id`, correlates tools by `tool_use_id`, observes `Agent`/`spawn_agent` plus subagent lifecycle events, and treats `SessionEnd` as root-session-only cleanup. It never reads `transcript_path`; the official documentation identifies transcript format as unstable.

`UserPromptSubmit` creates one task per user turn. Hook and MCP processes share the canonical `CODEX_HOME/codex-adaptive-router` root unless `CODEX_ADAPTIVE_ROUTER_DATA` explicitly overrides it. `PLUGIN_DATA` remains a read-only legacy source: an MCP-only `task_ref` can import its matching validated v2 task events from any exact `CODEX_HOME/plugins/data/codex-adaptive-router-*` root. Route IDs are preserved, event IDs and dedupe keys remain idempotent, and conflicting legacy route IDs fail closed.

`route_plan(task_ref=...)` returns the existing route. Asynchronous events are deduplicated and correlated by turn; append-only route events rebuild a missing ledger after a partial write. `Stop` records provisional evidence until objective verification or explicit user correction supplies quality.

## Privacy and learning gates

The salt is local-only and generated with restrictive permissions. Persisted evidence is limited to enums, bands, HMACs, IDs, timestamps, counts, and bounded route transitions.

Quality and risk gates precede resource minimization. The incumbent wins evidence ties, and an unexecuted cheaper candidate remains inconclusive. Policy proposals are axis-specific and always require human confirmation.
