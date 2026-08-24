# Codex-Gardener Bridge

## Boundary

Codex Adaptive Router is the runtime policy system. Codex-Gardener is the retrospective knowledge curator. They deliberately do not share mutable internals or depend on lifecycle-hook ordering.

```text
Adaptive Router event log
  -> evidence-backed route proposal
  -> optional shadow validation
  -> Gardener-compatible candidate fields
  -> Codex-Gardener curation
  -> explicit user approval
  -> Router policy revision
```

## Candidate contract

`adaptive_router.router_policy_status` returns `gardener_candidates` with only:

| Field | Purpose |
| --- | --- |
| `knowledge_scope` | `repository` until evidence spans sufficient projects, then `global` |
| `scope` | Short route class such as `adaptive routing: quant research` |
| `lesson` | Proposed invariant without prompt, source, or path data |
| `evidence` | Counts and aggregate confidence only |
| `recommended_target` | `skill` by default |
| `confidence` | Aggregate outcome confidence |

The Router does not call Gardener's private scripts or read its storage. During an explicit maintenance task, the primary agent can give these fields to Gardener's existing candidate flow.

## Promotion rules

1. Gardener evaluates duplicate, stale, conflicting, repository-specific, and global candidates.
2. A Gardener promotion is advisory to Router; it does not mutate Router policy.
3. Router independently starts shadow evaluation for an evidence-backed route change.
4. The user explicitly confirms the shadow-validated Router change.
5. Router creates a new policy revision and records the proposal ID.

This avoids a dangerous feedback loop where a single chat's success causes future chats to use a more expensive or less capable route without scrutiny.
