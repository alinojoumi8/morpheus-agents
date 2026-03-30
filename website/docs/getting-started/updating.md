---
sidebar_position: 3
title: "Updating & Uninstalling"
description: "How to update Morpheus Agent to the latest version or uninstall it"
---

# Updating & Uninstalling

## Updating

Update to the latest version with a single command:

```bash
morpheus update
```

This pulls the latest code, updates dependencies, and prompts you to configure any new options that were added since your last update.

:::tip
`morpheus update` automatically detects new configuration options and prompts you to add them. If you skipped that prompt, you can manually run `morpheus config check` to see missing options, then `morpheus config migrate` to interactively add them.
:::

### Updating from Messaging Platforms

You can also update directly from Telegram, Discord, Slack, or WhatsApp by sending:

```
/update
```

This pulls the latest code, updates dependencies, and restarts the gateway.

### Manual Update

If you installed manually (not via the quick installer):

```bash
cd /path/to/morpheus-agent
export VIRTUAL_ENV="$(pwd)/venv"

# Pull latest code and submodules
git pull origin main
git submodule update --init --recursive

# Reinstall (picks up new dependencies)
uv pip install -e ".[all]"
uv pip install -e "./tinker-atropos"

# Check for new config options
morpheus config check
morpheus config migrate   # Interactively add any missing options
```

---

## Uninstalling

```bash
morpheus uninstall
```

The uninstaller gives you the option to keep your configuration files (`~/.morpheus/`) for a future reinstall.

### Manual Uninstall

```bash
rm -f ~/.local/bin/morpheus
rm -rf /path/to/morpheus-agent
rm -rf ~/.morpheus            # Optional — keep if you plan to reinstall
```

:::info
If you installed the gateway as a system service, stop and disable it first:
```bash
morpheus gateway stop
# Linux: systemctl --user disable morpheus-gateway
# macOS: launchctl remove ai.morpheus.gateway
```
:::
