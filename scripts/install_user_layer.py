"""Explicit, backup-first installer for Adaptive Router's user-level Codex layer.

This script is intentionally never called by hooks. Run it manually after reviewing
the plugin. It copies namespaced custom agents and safely upserts only the router's
four [agents] defaults. Root model defaults require --set-root-model.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = path.with_name(path.name + f".adaptive-router.{stamp}.bak")
    suffix = 2
    while destination.exists():
        destination = path.with_name(path.name + f".adaptive-router.{stamp}.{suffix}.bak")
        suffix += 1
    shutil.copy2(path, destination)
    return destination


def replace_or_insert(lines: list[str], key: str, value: str, start: int, end: int) -> None:
    target = f"{key} = {value}"
    for index in range(start, end):
        if lines[index].strip().startswith(key + " ="):
            lines[index] = target
            return
    lines.insert(end, target)


def update_config(path: Path, set_root_model: bool) -> None:
    original = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = list(original)
    agents_start = next((index for index, line in enumerate(lines) if line.strip() == "[agents]"), None)
    if agents_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        agents_start = len(lines)
        lines.append("[agents]")
        agents_end = len(lines)
    else:
        agents_end = next(
            (index for index in range(agents_start + 1, len(lines)) if lines[index].lstrip().startswith("[")),
            len(lines),
        )
    for key, value in (
        ("enabled", "true"),
        ("max_concurrent_threads_per_session", "4"),
        ("default_subagent_model", '"gpt-5.6-terra"'),
        ("default_subagent_reasoning_effort", '"medium"'),
    ):
        replace_or_insert(lines, key, value, agents_start + 1, agents_end)
        agents_end = next(
            (index for index in range(agents_start + 1, len(lines)) if lines[index].lstrip().startswith("[")),
            len(lines),
        )
    if set_root_model:
        first_table = next((index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
        replace_or_insert(lines, "model", '"gpt-5.6"', 0, first_table)
        first_table = next((index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
        replace_or_insert(lines, "model_reasoning_effort", '"medium"', 0, first_table)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def copy_agents(target: Path) -> list[Path]:
    source = PLUGIN_ROOT / "templates" / "agents"
    target.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []
    for agent in sorted(source.glob("*.toml")):
        destination = target / agent.name
        if destination.exists():
            backup(destination)
        shutil.copy2(agent, destination)
        installed.append(destination)
    return installed


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Codex Adaptive Router's user-level layer with timestamped backups.")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--set-root-model", action="store_true", help="Set global root model to gpt-5.6 / medium as well as subagent defaults.")
    args = parser.parse_args()
    codex_home = args.codex_home.expanduser().resolve()
    config = codex_home / "config.toml"
    config_backup = backup(config)
    update_config(config, args.set_root_model)
    agents = copy_agents(codex_home / "agents")
    print(f"Updated {config}")
    if config_backup:
        print(f"Backup: {config_backup}")
    print("Installed agents:")
    for agent in agents:
        print(f"- {agent}")
    print("Restart Codex, enable/trust the plugin hooks, then start a new thread.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
