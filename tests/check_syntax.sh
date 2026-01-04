#!/bin/bash

# Quick syntax check wrapper script
# Validates all Python files can compile without syntax errors

# Change to repo root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# Use virtual environment Python if available, otherwise system Python
if [ -d ".venv" ]; then
    PYTHON_CMD=".venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

# Run the syntax checker
exec "$PYTHON_CMD" tests/check_syntax.py "$@"
