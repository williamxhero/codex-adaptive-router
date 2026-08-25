# Domain Context

## Purpose

Adaptive Router returns a deterministic execution plan. It separates who may own a conclusion from how much reasoning budget a legal model receives.

## Ubiquitous language

- **Capability**: the ordered model class `Luna < Terra < Sol`.
- **Authority**: the kind of responsibility held by a role: `evidence`, `implementation`, `decision`, or `audit`.
- **Capability floor**: the minimum model capability allowed for an authority. Evidence requires Luna, implementation requires Terra, and decision or audit requires Sol.
- **Reasoning budget**: the independent effort setting. More effort never raises model capability or authority.
- **Stage**: one bounded unit of a route plan: frame, collect, implement, verify, synthesize, or audit.
- **Route Plan v3**: the stored, immutable single-stage or staged plan, including execution target, dispatch readiness, delegation depth, writer ownership, and full-route token comparison.
- **Thin Root**: Root keeps intent, integration, acceptance, and the user-facing answer while bounded work is delegated when quality and total-token cost favor it.
- **Stage lease**: the transactional right to execute one stage attempt at a bounded depth and access mode.
- **Observed execution**: actual role/model/effort/target provenance, recorded separately from the planned route.
- **Capability exception**: a structured record that an explicit model request was below the route's floor and may only be used in a lower-authority worker stage while Sol retains final decision ownership.
- **Decision ownership**: a named Sol specialist may produce a delegated decision or audit result; the Root remains Sol Medium and owns final intent, integration, acceptance, and the user-facing conclusion.
- **Outcome signal**: the bounded classification `normal`, `exceptional_positive`, `exceptional_negative`, or `unknown`; it never contains a raw metric or result.
- **Audit follow-up**: an append-only required auditor stage produced by an exceptional-positive outcome when the immutable original plan lacked audit.
- **Exceptional result**: an unusually strong or consequential result that requires an adversarial Sol audit before acceptance.

## Invariants

1. Effort cannot compensate for a model below the authority floor.
2. Luna may collect evidence; Terra may implement a frozen specification; Sol owns decisions and audits.
3. Max and Ultra are never selected heuristically.
4. A stored task reference returns its original route plan without recomputation.
5. Routing evidence contains only bounded enums, bands, counts, IDs, and HMAC identities.
6. `direct` always denotes the current Root at Sol Medium; higher-budget Sol work uses a named specialist.
7. Complex worker-required work never silently falls back to Root when dispatch is blocked.
8. Delegation depth is at most two; one repository has at most one active writer lease.
9. Route plans are immutable. Remaining mismatched work freezes its lease and creates a new routed attempt.
10. Exact local token usage is actual-only; public evolution data receives bands/aggregates, never raw counts or text.
