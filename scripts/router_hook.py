"""Lifecycle adapter for Codex Adaptive Router plugin hooks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import router_core


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: router_hook.py <HookEvent>")
    event = sys.argv[1]
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise TypeError("hook input must be a JSON object")
        router_core.record_hook_event(event, payload)
        output = router_core.hook_context(event, payload)
        if output is not None:
            sys.stdout.write(json.dumps(output, ensure_ascii=False))
        else:
            sys.stdout.write(json.dumps({"continue": True}))
        return 0
    except (OSError, TimeoutError, TypeError, ValueError, json.JSONDecodeError) as error:
        # Hooks must fail open: routing evidence may never interrupt ordinary work.
        sys.stdout.write(json.dumps({"continue": True, "systemMessage": f"Adaptive Router hook skipped: {error}"}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
