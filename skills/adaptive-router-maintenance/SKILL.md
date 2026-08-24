---
name: adaptive-router-maintenance
description: Review local Adaptive Router evidence, run non-enforcing shadow evaluations, and hand proven cross-task routing lessons to Codex Gardener without exposing raw prompts or automatically changing global policy.
---

# Adaptive Router Maintenance

Use only when the user explicitly asks to review or improve Adaptive Router policy, or when a dedicated scheduled maintenance task is configured. Do not run it as part of ordinary project work.

## Inspect

1. Call `adaptive_router.router_policy_status`.
2. Review proposals by evidence count, confidence, distinct-project diversity, direction, and the source/target route.
3. Reject candidates that depend on a one-off user preference, a single repository convention, a transient outage, or an unverified quality claim.
4. Keep raw prompts, source paths, tool output, secrets, and transcript excerpts out of any learning summary.

## Shadow evaluation

1. Start shadow evaluation only for a `ready_for_shadow` proposal.
2. A shadow route is advisory only; the current policy stays active.
3. Record a shadow observation only when the actual result supplies evidence that the shadow route would or would not have been better.
4. Two shadow failures reject the proposal. The configured success threshold validates it for user confirmation.
5. Never call `confirm_policy_change` until the user explicitly directs the exact policy change.

## Gardener bridge

1. Read `gardener_candidates` from `adaptive_router.router_policy_status`.
2. If Codex-Gardener is installed, hand only eligible candidate fields to its normal curation flow: scope, lesson, evidence, recommended target, and confidence.
3. Let Gardener perform conflict checks, repository/global scope checks, and promotion review. Do not make the Router depend on hook ordering or Gardener's internal cache path.
4. Router owns route telemetry and executable policy. Gardener owns cross-session knowledge curation. A Gardener promotion is advisory until this Skill runs shadow validation and the user confirms activation.
