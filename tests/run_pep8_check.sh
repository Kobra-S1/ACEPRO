#!/bin/bash

# Exit on error
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo -e "${BLUE}Running PEP 8 Style Check${NC}"
echo "=========================================="
echo ""

# Change to repo root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# Activate virtual environment if it exists
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
    echo -e "${BLUE}Activating virtual environment...${NC}"
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
fi

# Run flake8 PEP 8 checks (using .flake8 config)
flake8 --statistics extras/ace

FLAKE8_EXIT=$?

echo ""
if [ $FLAKE8_EXIT -eq 0 ]; then
    echo -e "${GREEN}✓ All PEP 8 checks passed!${NC}"
else
    echo -e "${RED}✗ PEP 8 style issues detected${NC}"
fi
echo "=========================================="

exit $FLAKE8_EXIT
