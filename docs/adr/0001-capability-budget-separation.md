# ADR 0001: Separate Capability from Reasoning Budget

- Status: Accepted
- Date: 2026-08-25
- Decision owners: Adaptive Router maintainers

## Context

Earlier profiles paired a role with a model and an effort default, which made it too easy to read high effort as a substitute for model capability. That ambiguity is unsafe for research, architecture, statistical judgment, market semantics, and adversarial review.

## Decision

Use the capability lattice `Luna < Terra < Sol` independently from the effort lattice. Every role declares an authority and capability floor. Evidence has a Luna floor, frozen-spec implementation has a Terra floor, and decision or audit authority has a Sol floor. Route planning computes the capability floor before it computes and clamps deterministic effort.

Route Plan v2 may stage work across bounded evidence and implementation roles, but the Sol primary thread retains final framing, integration, synthesis, and conclusions. Explicit below-floor model requests are represented as capability exceptions and may only be used at a legal lower-authority worker stage. Max and Ultra require an explicit constraint or a human-confirmed policy override.

## Alternatives considered

- Treat high effort as equivalent to a stronger model. Rejected because reasoning time cannot grant missing authority or capability.
- Route every task entirely to Sol. Rejected because bounded evidence and frozen implementation can use cheaper specialists without transferring decision ownership.
- Learn model-effort combinations automatically. Rejected for v1.2 because confounding and sparse evidence make autonomous policy changes unsafe.

## Consequences

- Profiles use schema v3 and explicitly declare authority, floor, legal models, effort bands, and Sol escalation conditions.
- Decision Features v2 and Route Plan v2 make effort triggers and stage ownership inspectable.
- Outcome Intelligence must attribute model and effort failures independently and mark multi-axis changes as confounded.
- Existing v1/v2 evidence remains read-only compatible; new evidence uses schema v3.
