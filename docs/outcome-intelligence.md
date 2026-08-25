# Outcome Intelligence v1.3.0

## Engine seam

`RouterEngine` keeps planning, lifecycle observation, transactional stage leases, outcome finalization, policy evaluation, and status behind one deep seam. Route Plan v3 is immutable; dispatch attempts and actual observations evolve separately.

## Lifecycle and leases

Hooks and MCP use one canonical data root and retain the v1.1.1 task-ref/legacy-store behavior. Agent identities and repository scope are HMAC-bound. A delegated stage is claimed atomically, enforces depth two, one default active specialist, at most three plan-declared independent read-only siblings with distinct claim keys, and one active writer in the logical repository tree. A mismatched remainder freezes its lease and reroutes without modifying the original plan.

`SubagentStop` proves only lifecycle completion. It never proves objective verification, boundary compliance, scope compliance, a passed quality gate, or archive eligibility.

## Local Evidence v4

Local v4 outcomes record dispatch target, observed role/model/effort/target, plan match, boundary/scope/verification/archive state, delegation depth, and an HMAC lease reference. Exact input/output/total tokens are accepted only from stable Codex/provider usage or an exact caller report. Hook-only runs stay unknown rather than estimated-as-actual.

## Public projection

GitHub sync projects v4 before upload. It removes exact local tokens, token estimates, titles, objectives, prompts, paths, code, tool I/O, and transcripts. Public batches contain only token bands/aggregates, bounded enums, HMAC identities, UUID/sequence scaffolding, counts, and timestamps. Evidence v1-v3 and the existing hash chain remain byte-preserved and read-only.

## Learning safeguards

Learning uses observed execution, not the planned tuple, and only the route's unique primary stage is eligible. Model evidence holds role, effort, execution target, depth, stage, and task class fixed; effort evidence holds role, model, execution target, depth, stage, and task class fixed. Plan deviation, failed/unknown boundaries or verification, worker or lease failures, non-adequate context/tool data, and multi-axis changes cannot create a proposal or a comparable shadow result. Repeated objective evidence, non-enforcing shadow evaluation, and explicit human confirmation remain mandatory; policy never promotes itself.

An exceptional-positive result creates a claimable Sol XHigh audit lease without rewriting the original plan. Its outcome must bind the completed, fully gated observed lease before acceptance. A visible task is archive eligible only after successful terminal state and passed quality, boundary, scope, verification, and required audit gates.
