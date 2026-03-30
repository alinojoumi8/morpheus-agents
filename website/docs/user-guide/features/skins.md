---
sidebar_position: 10
title: "Skins & Themes"
description: "Customize the Morpheus CLI with built-in and user-defined skins"
---

# Skins & Themes

Skins control the **visual presentation** of the Morpheus CLI: banner colors, spinner faces and verbs, response-box labels, branding text, and the tool activity prefix.

Conversational style and visual style are separate concepts:

- **Personality** changes the agent's tone and wording.
- **Skin** changes the CLI's appearance.

## Change skins

```bash
/skin                # show the current skin and list available skins
/skin ares           # switch to a built-in skin
/skin mytheme        # switch to a custom skin from ~/.morpheus/skins/mytheme.yaml
```

Or set the default skin in `~/.morpheus/config.yaml`:

```yaml
display:
  skin: default
```

## Built-in skins

| Skin | Description | Agent branding |
|------|-------------|----------------|
| `default` | Classic Morpheus — gold and kawaii | `Morpheus Agent` |
| `ares` | War-god theme — crimson and bronze | `Ares Agent` |
| `mono` | Monochrome — clean grayscale | `Morpheus Agent` |
| `slate` | Cool blue — developer-focused | `Morpheus Agent` |
| `poseidon` | Ocean-god theme — deep blue and seafoam | `Poseidon Agent` |
| `sisyphus` | Sisyphean theme — austere grayscale with persistence | `Sisyphus Agent` |
| `charizard` | Volcanic theme — burnt orange and ember | `Charizard Agent` |

## What a skin can customize

| Area | Keys |
|------|------|
| Banner + response colors | `colors.banner_*`, `colors.response_border` |
| Spinner animation | `spinner.waiting_faces`, `spinner.thinking_faces`, `spinner.thinking_verbs`, `spinner.wings` |
| Branding text | `branding.agent_name`, `branding.welcome`, `branding.response_label`, `branding.prompt_symbol` |
| Tool activity prefix | `tool_prefix` |

## Custom skins

Create YAML files under `~/.morpheus/skins/`. User skins inherit missing values from the built-in `default` skin.

```yaml
name: cyberpunk
description: Neon terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

## Operational notes

- Built-in skins load from `morpheus_cli/skin_engine.py`.
- Unknown skins automatically fall back to `default`.
- `/skin` updates the active CLI theme immediately for the current session.