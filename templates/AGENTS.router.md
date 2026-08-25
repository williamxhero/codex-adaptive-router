## Codex Adaptive Routing

- Treat an explicit user choice of model, reasoning effort, agent, or no-delegation as the highest routing override after system safety constraints.
- For non-trivial tasks, use `$codex-adaptive-router:adaptive-routing` and follow the returned route or explain why a safer route is necessary.
- Luna agents produce bounded evidence only; they do not own architecture, statistical, research, or high-impact conclusions.
- Terra owns implementation after the specification is frozen. If it finds an unresolved specification or semantic decision, return it to a Sol route.
- Do not automatically use Max or Ultra. Do not automatically apply learned policy changes; use shadow evaluation and explicit user confirmation.
- Claim every delegated stage before execution. Root is depth 0, child depth 1, and grandchild depth 2; never delegate beyond depth 2.
- Complex work blocked by an unavailable worker stays blocked; Root must not silently take it over.
- Keep one writer per logical repository and at most three explicitly independent read-only siblings.
- Record planned and observed role/model/effort/target separately, and never report estimated tokens as exact usage.
