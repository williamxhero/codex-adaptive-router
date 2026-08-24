---
name: adaptive-router-install
description: Install or remove the Codex Adaptive Router personal configuration layer with timestamped backups and without touching project configuration.
---

# Adaptive Router Install

Use only when the user explicitly asks to install, update, or remove the global Adaptive Router layer.

## Install

1. Inspect `~/.codex/config.toml` and `~/.codex/agents/` first.
2. Explain that the installer updates only `[agents]` defaults and copies namespaced `router_*.toml` agents. It writes timestamped backups of every file it replaces.
3. Run:

```powershell
python <plugin-root>\scripts\install_user_layer.py
```

4. Add `--set-root-model` only when the user wants every new primary thread to default to Sol Medium.
5. Restart Codex, trust the plugin hooks, and start a new thread before verifying agent discovery.

## Uninstall

1. Run the uninstall script without `--apply` to show its exact targets.
2. Get confirmation if it lists any unexpected file.
3. Re-run with `--apply` to remove only `router_*.toml` custom-agent files.
4. Do not delete or rewrite global `config.toml`; restore the timestamped backup if the user wants to undo those defaults.
