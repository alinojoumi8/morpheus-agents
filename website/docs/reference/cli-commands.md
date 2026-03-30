---
sidebar_position: 1
title: "CLI Commands Reference"
description: "Authoritative reference for Morpheus terminal commands and command families"
---

# CLI Commands Reference

This page covers the **terminal commands** you run from your shell.

For in-chat slash commands, see [Slash Commands Reference](./slash-commands.md).

## Global entrypoint

```bash
morpheus [global-options] <command> [subcommand/options]
```

### Global options

| Option | Description |
|--------|-------------|
| `--version`, `-V` | Show version and exit. |
| `--resume <session>`, `-r <session>` | Resume a previous session by ID or title. |
| `--continue [name]`, `-c [name]` | Resume the most recent session, or the most recent session matching a title. |
| `--worktree`, `-w` | Start in an isolated git worktree for parallel-agent workflows. |
| `--yolo` | Bypass dangerous-command approval prompts. |
| `--pass-session-id` | Include the session ID in the agent's system prompt. |

## Top-level commands

| Command | Purpose |
|---------|---------|
| `morpheus chat` | Interactive or one-shot chat with the agent. |
| `morpheus model` | Interactively choose the default provider and model. |
| `morpheus gateway` | Run or manage the messaging gateway service. |
| `morpheus setup` | Interactive setup wizard for all or part of the configuration. |
| `morpheus whatsapp` | Configure and pair the WhatsApp bridge. |
| `morpheus login` / `logout` | Authenticate with OAuth-backed providers. |
| `morpheus status` | Show agent, auth, and platform status. |
| `morpheus cron` | Inspect and tick the cron scheduler. |
| `morpheus doctor` | Diagnose config and dependency issues. |
| `morpheus config` | Show, edit, migrate, and query configuration files. |
| `morpheus pairing` | Approve or revoke messaging pairing codes. |
| `morpheus skills` | Browse, install, publish, audit, and configure skills. |
| `morpheus honcho` | Manage Honcho cross-session memory integration. |
| `morpheus acp` | Run Morpheus as an ACP server for editor integration. |
| `morpheus tools` | Configure enabled tools per platform. |
| `morpheus sessions` | Browse, export, prune, rename, and delete sessions. |
| `morpheus insights` | Show token/cost/activity analytics. |
| `morpheus claw` | OpenClaw migration helpers. |
| `morpheus version` | Show version information. |
| `morpheus update` | Pull latest code and reinstall dependencies. |
| `morpheus uninstall` | Remove Morpheus from the system. |

## `morpheus chat`

```bash
morpheus chat [options]
```

Common options:

| Option | Description |
|--------|-------------|
| `-q`, `--query "..."` | One-shot, non-interactive prompt. |
| `-m`, `--model <model>` | Override the model for this run. |
| `-t`, `--toolsets <csv>` | Enable a comma-separated set of toolsets. |
| `--provider <provider>` | Force a provider: `auto`, `openrouter`, `nous`, `openai-codex`, `copilot`, `copilot-acp`, `anthropic`, `huggingface`, `zai`, `kimi-coding`, `minimax`, `minimax-cn`, `kilocode`. |
| `-s`, `--skills <name>` | Preload one or more skills for the session (can be repeated or comma-separated). |
| `-v`, `--verbose` | Verbose output. |
| `-Q`, `--quiet` | Programmatic mode: suppress banner/spinner/tool previews. |
| `--resume <session>` / `--continue [name]` | Resume a session directly from `chat`. |
| `--worktree` | Create an isolated git worktree for this run. |
| `--checkpoints` | Enable filesystem checkpoints before destructive file changes. |
| `--yolo` | Skip approval prompts. |
| `--pass-session-id` | Pass the session ID into the system prompt. |

Examples:

```bash
morpheus
morpheus chat -q "Summarize the latest PRs"
morpheus chat --provider openrouter --model anthropic/claude-sonnet-4.6
morpheus chat --toolsets web,terminal,skills
morpheus chat --quiet -q "Return only JSON"
morpheus chat --worktree -q "Review this repo and open a PR"
```

## `morpheus model`

Interactive provider + model selector.

```bash
morpheus model
```

Use this when you want to:
- switch default providers
- log into OAuth-backed providers during model selection
- pick from provider-specific model lists
- configure a custom/self-hosted endpoint
- save the new default into config

### `/model` slash command (mid-session)

Switch models without leaving a session:

```
/model                              # Show current model and available options
/model claude-sonnet-4              # Switch model (auto-detects provider)
/model zai:glm-5                    # Switch provider and model
/model custom:qwen-2.5              # Use model on your custom endpoint
/model custom                       # Auto-detect model from custom endpoint
/model custom:local:qwen-2.5        # Use a named custom provider
/model openrouter:anthropic/claude-sonnet-4  # Switch back to cloud
```

Provider and base URL changes are persisted to `config.yaml` automatically. When switching away from a custom endpoint, the stale base URL is cleared to prevent it leaking into other providers.

## `morpheus gateway`

```bash
morpheus gateway <subcommand>
```

Subcommands:

| Subcommand | Description |
|------------|-------------|
| `run` | Run the gateway in the foreground. |
| `start` | Start the installed gateway service. |
| `stop` | Stop the service. |
| `restart` | Restart the service. |
| `status` | Show service status. |
| `install` | Install as a user service (`systemd` on Linux, `launchd` on macOS). |
| `uninstall` | Remove the installed service. |
| `setup` | Interactive messaging-platform setup. |

## `morpheus setup`

```bash
morpheus setup [model|terminal|gateway|tools|agent] [--non-interactive] [--reset]
```

Use the full wizard or jump into one section:

| Section | Description |
|---------|-------------|
| `model` | Provider and model setup. |
| `terminal` | Terminal backend and sandbox setup. |
| `gateway` | Messaging platform setup. |
| `tools` | Enable/disable tools per platform. |
| `agent` | Agent behavior settings. |

Options:

| Option | Description |
|--------|-------------|
| `--non-interactive` | Use defaults / environment values without prompts. |
| `--reset` | Reset configuration to defaults before setup. |

## `morpheus whatsapp`

```bash
morpheus whatsapp
```

Runs the WhatsApp pairing/setup flow, including mode selection and QR-code pairing.

## `morpheus login` / `morpheus logout`

```bash
morpheus login [--provider nous|openai-codex] [--portal-url ...] [--inference-url ...]
morpheus logout [--provider nous|openai-codex]
```

`login` supports:
- Nous Portal OAuth/device flow
- OpenAI Codex OAuth/device flow

Useful options for `login`:
- `--no-browser`
- `--timeout <seconds>`
- `--ca-bundle <pem>`
- `--insecure`

## `morpheus status`

```bash
morpheus status [--all] [--deep]
```

| Option | Description |
|--------|-------------|
| `--all` | Show all details in a shareable redacted format. |
| `--deep` | Run deeper checks that may take longer. |

## `morpheus cron`

```bash
morpheus cron <list|create|edit|pause|resume|run|remove|status|tick>
```

| Subcommand | Description |
|------------|-------------|
| `list` | Show scheduled jobs. |
| `create` / `add` | Create a scheduled job from a prompt, optionally attaching one or more skills via repeated `--skill`. |
| `edit` | Update a job's schedule, prompt, name, delivery, repeat count, or attached skills. Supports `--clear-skills`, `--add-skill`, and `--remove-skill`. |
| `pause` | Pause a job without deleting it. |
| `resume` | Resume a paused job and compute its next future run. |
| `run` | Trigger a job on the next scheduler tick. |
| `remove` | Delete a scheduled job. |
| `status` | Check whether the cron scheduler is running. |
| `tick` | Run due jobs once and exit. |

## `morpheus doctor`

```bash
morpheus doctor [--fix]
```

| Option | Description |
|--------|-------------|
| `--fix` | Attempt automatic repairs where possible. |

## `morpheus config`

```bash
morpheus config <subcommand>
```

Subcommands:

| Subcommand | Description |
|------------|-------------|
| `show` | Show current config values. |
| `edit` | Open `config.yaml` in your editor. |
| `set <key> <value>` | Set a config value. |
| `path` | Print the config file path. |
| `env-path` | Print the `.env` file path. |
| `check` | Check for missing or stale config. |
| `migrate` | Add newly introduced options interactively. |

## `morpheus pairing`

```bash
morpheus pairing <list|approve|revoke|clear-pending>
```

| Subcommand | Description |
|------------|-------------|
| `list` | Show pending and approved users. |
| `approve <platform> <code>` | Approve a pairing code. |
| `revoke <platform> <user-id>` | Revoke a user's access. |
| `clear-pending` | Clear pending pairing codes. |

## `morpheus skills`

```bash
morpheus skills <subcommand>
```

Subcommands:

| Subcommand | Description |
|------------|-------------|
| `browse` | Paginated browser for skill registries. |
| `search` | Search skill registries. |
| `install` | Install a skill. |
| `inspect` | Preview a skill without installing it. |
| `list` | List installed skills. |
| `check` | Check installed hub skills for upstream updates. |
| `update` | Reinstall hub skills with upstream changes when available. |
| `audit` | Re-scan installed hub skills. |
| `uninstall` | Remove a hub-installed skill. |
| `publish` | Publish a skill to a registry. |
| `snapshot` | Export/import skill configurations. |
| `tap` | Manage custom skill sources. |
| `config` | Interactive enable/disable configuration for skills by platform. |

Common examples:

```bash
morpheus skills browse
morpheus skills browse --source official
morpheus skills search react --source skills-sh
morpheus skills search https://mintlify.com/docs --source well-known
morpheus skills inspect official/security/1password
morpheus skills inspect skills-sh/vercel-labs/json-render/json-render-react
morpheus skills install official/migration/openclaw-migration
morpheus skills install skills-sh/anthropics/skills/pdf --force
morpheus skills check
morpheus skills update
morpheus skills config
```

Notes:
- `--force` can override non-dangerous policy blocks for third-party/community skills.
- `--force` does not override a `dangerous` scan verdict.
- `--source skills-sh` searches the public `skills.sh` directory.
- `--source well-known` lets you point Morpheus at a site exposing `/.well-known/skills/index.json`.

## `morpheus honcho`

```bash
morpheus honcho <subcommand>
```

Subcommands:

| Subcommand | Description |
|------------|-------------|
| `setup` | Interactive Honcho setup wizard. |
| `status` | Show current Honcho config and connection status. |
| `sessions` | List known Honcho session mappings. |
| `map` | Map the current directory to a Honcho session name. |
| `peer` | Show or update peer names and dialectic reasoning level. |
| `mode` | Show or set memory mode: `hybrid`, `honcho`, or `local`. |
| `tokens` | Show or set token budgets for context and dialectic. |
| `identity` | Seed or show the AI peer identity representation. |
| `migrate` | Migration guide from openclaw-honcho to Morpheus Honcho. |

## `morpheus acp`

```bash
morpheus acp
```

Starts Morpheus as an ACP (Agent Client Protocol) stdio server for editor integration.

Related entrypoints:

```bash
morpheus-acp
python -m acp_adapter
```

Install support first:

```bash
pip install -e '.[acp]'
```

See [ACP Editor Integration](../user-guide/features/acp.md) and [ACP Internals](../developer-guide/acp-internals.md).

## `morpheus mcp`

```bash
morpheus mcp <subcommand>
```

Manage MCP (Model Context Protocol) server configurations.

| Subcommand | Description |
|------------|-------------|
| `add <name> [--url URL] [--command CMD] [--args ...] [--auth oauth\|header]` | Add an MCP server with automatic tool discovery. |
| `remove <name>` (alias: `rm`) | Remove an MCP server from config. |
| `list` (alias: `ls`) | List configured MCP servers. |
| `test <name>` | Test connection to an MCP server. |
| `configure <name>` (alias: `config`) | Toggle tool selection for a server. |

See [MCP Config Reference](./mcp-config-reference.md) and [Use MCP with Morpheus](../guides/use-mcp-with-morpheus.md).

## `morpheus plugins`

```bash
morpheus plugins <subcommand>
```

Manage Morpheus Agent plugins.

| Subcommand | Description |
|------------|-------------|
| `install <identifier> [--force]` | Install a plugin from a Git URL or `owner/repo`. |
| `update <name>` | Pull latest changes for an installed plugin. |
| `remove <name>` (aliases: `rm`, `uninstall`) | Remove an installed plugin. |
| `list` (alias: `ls`) | List installed plugins. |

See [Plugins](../user-guide/features/plugins.md) and [Build a Morpheus Plugin](../guides/build-a-morpheus-plugin.md).

## `morpheus tools`

```bash
morpheus tools [--summary]
```

| Option | Description |
|--------|-------------|
| `--summary` | Print the current enabled-tools summary and exit. |

Without `--summary`, this launches the interactive per-platform tool configuration UI.

## `morpheus sessions`

```bash
morpheus sessions <subcommand>
```

Subcommands:

| Subcommand | Description |
|------------|-------------|
| `list` | List recent sessions. |
| `browse` | Interactive session picker with search and resume. |
| `export <output> [--session-id ID]` | Export sessions to JSONL. |
| `delete <session-id>` | Delete one session. |
| `prune` | Delete old sessions. |
| `stats` | Show session-store statistics. |
| `rename <session-id> <title>` | Set or change a session title. |

## `morpheus insights`

```bash
morpheus insights [--days N] [--source platform]
```

| Option | Description |
|--------|-------------|
| `--days <n>` | Analyze the last `n` days (default: 30). |
| `--source <platform>` | Filter by source such as `cli`, `telegram`, or `discord`. |

## `morpheus claw`

```bash
morpheus claw migrate
```

Used to migrate settings, memories, skills, and keys from OpenClaw to Morpheus.

## Maintenance commands

| Command | Description |
|---------|-------------|
| `morpheus version` | Print version information. |
| `morpheus update` | Pull latest changes and reinstall dependencies. |
| `morpheus uninstall [--full] [--yes]` | Remove Morpheus, optionally deleting all config/data. |

## See also

- [Slash Commands Reference](./slash-commands.md)
- [CLI Interface](../user-guide/cli.md)
- [Sessions](../user-guide/sessions.md)
- [Skills System](../user-guide/features/skills.md)
- [Skins & Themes](../user-guide/features/skins.md)
