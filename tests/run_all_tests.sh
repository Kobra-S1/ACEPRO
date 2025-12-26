#!/bin/bash

# Exit if any command fails in a pipeline we explicitly check,
# but don't use "set -e" so we can print friendly errors.
# set -euo pipefail  # <- you can enable this once everything is stable

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "========================================"
echo "  ACE Pro Test Suite - Linux/macOS"
echo "========================================"
echo ""

# Detect Python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}Error: Python not found. Please install Python 3.7+${NC}"
    exit 1
fi

echo "Using Python: $PYTHON_CMD"
$PYTHON_CMD --version
echo ""

# Change to repo root (parent of this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

echo "Working directory: $(pwd)"
echo ""

# Create virtual environment if it doesn't exist
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    $PYTHON_CMD -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to create virtual environment${NC}"
        echo -e "${YELLOW}Trying with --system-site-packages...${NC}"
        $PYTHON_CMD -m venv --system-site-packages "$VENV_DIR"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to create virtual environment. Please install python3-venv:${NC}"
            echo "sudo apt install python3-venv python3-full"
            exit 1
        fi
    fi
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# Make sure pip exists in this venv
echo -e "${YELLOW}Checking/bootstrapping pip in venv...${NC}"
if ! python -m pip --version >/dev/null 2>&1; then
    echo -e "${YELLOW}pip not found in venv, bootstrapping with ensurepip...${NC}"
    python -m ensurepip --upgrade
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to bootstrap pip in the virtual environment.${NC}"
        echo "Your Python may have been built without ensurepip or has a broken pip."
        deactivate
        exit 1
    fi
fi

# Upgrade pip in venv (show output so you see what's going on)
echo -e "${YELLOW}Upgrading pip...${NC}"
python -m pip install --upgrade pip
if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to upgrade pip${NC}"
    deactivate
    exit 1
fi

# Install dependencies in venv
echo -e "${YELLOW}Installing dependencies...${NC}"

# Install runtime dependencies
if [ -f "requirements.txt" ]; then
    echo "Installing runtime dependencies from requirements.txt..."
    python -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to install runtime dependencies${NC}"
        deactivate
        exit 1
    fi
else
    echo -e "${YELLOW}Warning: requirements.txt not found, skipping${NC}"
fi

# Install test dependencies (optional)
if [ -f "tests/requirements-test.txt" ]; then
    echo "Installing test dependencies from tests/requirements-test.txt..."
    python -m pip install -r tests/requirements-test.txt
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to install test dependencies${NC}"
        deactivate
        exit 1
    fi
else
    echo "Installing minimal test dependencies..."
    python -m pip install pytest pytest-cov pytest-mock
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to install test dependencies${NC}"
        deactivate
        exit 1
    fi
fi

echo -e "${GREEN}Dependencies installed successfully${NC}"
echo ""

# Set PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Run flake8 critical checks
echo "=========================================="
echo -e "${BLUE}Running flake8 critical checks${NC}"
echo "=========================================="
echo ""

flake8 --select=E9,F63,F7,F82 \
       --exclude=".venv,*/.venv/*" \
       extras/ace

FLAKE8_EXIT=$?

if [ $FLAKE8_EXIT -eq 0 ]; then
    echo -e "${GREEN}✓ No critical issues found${NC}"
else
    echo -e "${RED}✗ Critical issues detected - fix before running tests${NC}"
    deactivate
    exit $FLAKE8_EXIT
fi
echo ""

# Run tests with coverage (store in .tmp folder)
echo -e "${BLUE}Running all tests with coverage...${NC}"
echo "=========================================="
mkdir -p .tmp
pytest . -v --tb=short --color=yes \
    --cov=ace \
    --cov=extras/ace \
    --cov-report=term-missing \
    --cov-report=html:../.tmp/htmlcov \
    --cov-config=tests/.coveragerc

# Capture exit code
TEST_EXIT_CODE=$?

# Deactivate virtual environment
deactivate

echo ""
echo "=========================================="
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    echo ""
    echo -e "${BLUE}Coverage report generated: .tmp/htmlcov/index.html${NC}"
else
    echo -e "${RED}Some tests failed (exit code: $TEST_EXIT_CODE)${NC}"
fi
echo "=========================================="

exit $TEST_EXIT_CODE
