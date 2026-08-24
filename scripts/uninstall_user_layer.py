"""Remove only namespaced Adaptive Router agent files from a selected Codex home."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove only router_*.toml agents installed by Codex Adaptive Router.")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--apply", action="store_true", help="Actually remove files. Without this flag the script only reports targets.")
    args = parser.parse_args()
    agents = args.codex_home.expanduser().resolve() / "agents"
    targets = sorted(agents.glob("router_*.toml")) if agents.is_dir() else []
    for target in targets:
        print(target)
        if args.apply:
            target.unlink()
    if not args.apply:
        print("Dry run only. Re-run with --apply to remove these namespaced agent files.")
    print("Global config.toml is intentionally not edited; restore its timestamped backup if required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
