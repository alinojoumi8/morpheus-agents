#!/bin/bash
# ============================================================================
# Morpheus Agent — One-command launcher
# ============================================================================
# Clone the repo and run this script. It handles everything:
#   1. Installs uv (if missing)
#   2. Creates venv + installs dependencies (first run only)
#   3. Runs the setup wizard (first run only)
#   4. Starts Morpheus Agent
#
# Usage:
#   ./run.sh                  # Interactive CLI
#   ./run.sh setup            # Re-run setup wizard
#   ./run.sh gateway          # Start messaging gateway
#   ./run.sh --help           # Show all commands
# ============================================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"
PYTHON_VERSION="3.11"

# ============================================================================
# Step 1: Ensure uv is available
# ============================================================================

_find_uv() {
    if command -v uv &> /dev/null; then echo "uv"; return; fi
    if [ -x "$HOME/.local/bin/uv" ]; then echo "$HOME/.local/bin/uv"; return; fi
    if [ -x "$HOME/.cargo/bin/uv" ]; then echo "$HOME/.cargo/bin/uv"; return; fi
    echo ""
}

UV_CMD=$(_find_uv)

if [ -z "$UV_CMD" ]; then
    echo -e "${CYAN}⚡ Installing uv (fast Python package manager)...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
    UV_CMD=$(_find_uv)
    if [ -z "$UV_CMD" ]; then
        echo -e "${RED}Failed to install uv. Visit https://docs.astral.sh/uv/${NC}"
        exit 1
    fi
fi

# ============================================================================
# Step 2: Create venv + install (first run only)
# ============================================================================

if [ ! -d "$VENV_DIR" ] || [ ! -f "$VENV_DIR/bin/python" ]; then
    echo ""
    echo -e "${BOLD}${CYAN}⚡ MORPHEUS AGENT — First-time setup${NC}"
    echo ""

    # Ensure Python is available
    if ! $UV_CMD python find "$PYTHON_VERSION" &> /dev/null; then
        echo -e "${CYAN}→${NC} Installing Python $PYTHON_VERSION..."
        $UV_CMD python install "$PYTHON_VERSION"
    fi

    # Create venv
    echo -e "${CYAN}→${NC} Creating virtual environment..."
    $UV_CMD venv "$VENV_DIR" --python "$PYTHON_VERSION"

    # Install dependencies
    echo -e "${CYAN}→${NC} Installing dependencies (this may take a minute)..."
    export VIRTUAL_ENV="$VENV_DIR"

    if [ -f "uv.lock" ]; then
        UV_PROJECT_ENVIRONMENT="$VENV_DIR" $UV_CMD sync --all-extras --locked 2>/dev/null || \
            $UV_CMD pip install -e ".[intelligence]" 2>/dev/null || \
            $UV_CMD pip install -e "."
    else
        $UV_CMD pip install -e ".[intelligence]" 2>/dev/null || \
            $UV_CMD pip install -e "."
    fi

    echo -e "${GREEN}✓${NC} Dependencies installed"

    # Create .env if missing
    if [ ! -f "$SCRIPT_DIR/.env" ] && [ -f "$SCRIPT_DIR/.env.example" ]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        echo -e "${GREEN}✓${NC} Created .env from template"
    fi

    # Seed skills
    MORPHEUS_SKILLS_DIR="${MORPHEUS_HOME:-$HOME/.morpheus}/skills"
    mkdir -p "$MORPHEUS_SKILLS_DIR"
    if [ -d "$SCRIPT_DIR/skills" ]; then
        "$VENV_DIR/bin/python" "$SCRIPT_DIR/tools/skills_sync.py" 2>/dev/null || \
            cp -rn "$SCRIPT_DIR/skills/"* "$MORPHEUS_SKILLS_DIR/" 2>/dev/null || true
    fi

    echo ""
    echo -e "${GREEN}✓ Setup complete!${NC}"
    echo ""

    # Check if morpheus home exists (has been set up before)
    MORPHEUS_HOME_DIR="${MORPHEUS_HOME:-$HOME/.morpheus}"
    if [ ! -f "$MORPHEUS_HOME_DIR/config.yaml" ]; then
        echo -e "${YELLOW}No API keys configured yet. Running setup wizard...${NC}"
        echo ""
        "$VENV_DIR/bin/python" -m morpheus_cli.main setup
    fi

    echo ""
fi

# ============================================================================
# Step 3: Run Morpheus
# ============================================================================

exec "$VENV_DIR/bin/python" -m morpheus_cli.main "$@"
