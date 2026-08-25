# ADR 0002: Thin Root and Recursive Dispatch

- Status: Accepted
- Date: 2026-08-25
- Release: 1.3.0

## Decision

Profile v4 and Route Plan v3 evaluate explicit constraints, capability floors, quality and isolation requirements, executable candidates, and only then expected full-route tokens. Complex tasks default to a subagent. Long-running, cross-project, or explicitly isolated tasks use a visible task. A direct exception is legal only when Root Sol Medium satisfies the required quality and capability and direct execution beats routing by the configured absolute and relative token margins.

Delegated stages use immutable plan identities and transactional leases. Root is depth zero, children depth one, and grandchildren depth two. A parent has one active specialist by default; at most three plan-declared independent read-only children with distinct claim keys may run concurrently. A logical repository has one active writer. Work that no longer matches a lease freezes and reroutes without modifying the original plan.

Planned and observed execution are separate. Outcome Intelligence v4 records actual role/model/effort/target, plan match, boundary/scope/verification/archive state, lease provenance, and exact local token usage only when a stable source provides it. Public GitHub projection removes exact tokens and operational/free-text fields.

## Consequences

- Worker unavailability blocks complex work rather than causing a silent Root fallback.
- Only Root may create visible tasks; successful, fully quality-gated tasks become archive eligible.
- Quantitative attribution uses `router_quant_researcher` (Sol High), bounded Luna/Terra work, Sol synthesis, and conditional Sol XHigh audit.
- Max and Ultra remain human constrained and are never selected automatically.
- Evidence v1-v3 and published hash chains remain read-only.
