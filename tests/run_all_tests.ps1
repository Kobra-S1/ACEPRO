#!/usr/bin/env pwsh
# =============================================================================
# ACE Pro Test Runner - Windows (PowerShell)
# =============================================================================
# Runs all unit tests for the ACE Pro Klipper module
#
# Usage:
#   .\run_all_tests.ps1           # Run all tests
#   .\run_all_tests.ps1 -Verbose  # Run with verbose output
#   .\run_all_tests.ps1 -TestFile test_manager_adapted.py  # Run single file
#
# Requirements:
#   - Python 3.7+
#   - pytest (pip install pytest)
#
# =============================================================================

param(
    [switch]$Verbose,
    [string]$TestFile = "",
    [switch]$Coverage,
    [switch]$Help
)

# Show help
if ($Help) {
    Write-Host @"
ACE Pro Test Runner

Usage:
    .\run_all_tests.ps1 [options]

Options:
    -Verbose      Show detailed test output
    -TestFile     Run specific test file (e.g., test_manager_adapted.py)
    -Coverage     Generate code coverage report
    -Help         Show this help message

Examples:
    .\run_all_tests.ps1                          # Run all tests
    .\run_all_tests.ps1 -Verbose                 # Verbose output
    .\run_all_tests.ps1 -TestFile test_manager_adapted.py
    .\run_all_tests.ps1 -Coverage                # With coverage

"@
    exit 0
}

# Colors for output
function Write-Success { Write-Host $args -ForegroundColor Green }
function Write-Info { Write-Host $args -ForegroundColor Cyan }
function Write-Error { Write-Host $args -ForegroundColor Red }
function Write-Warning { Write-Host $args -ForegroundColor Yellow }

Write-Info "========================================"
Write-Info "  ACE Pro Test Suite - Windows"
Write-Info "========================================"
Write-Host ""

# Get repo root (parent directory of this script)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Push-Location $repoRoot

# Check if Python is available
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $pythonCmd = $cmd
        break
    }
}

if (-not $pythonCmd) {
    Write-Error "ERROR: Python not found in PATH"
    Write-Error "Please install Python 3.7+ and add to PATH"
    exit 1
}

Write-Info "Using Python: $pythonCmd"
& $pythonCmd --version
Write-Host ""
Write-Info "Working directory: $(Get-Location)"
Write-Host ""

# Install/update dependencies
Write-Info "Installing dependencies..."

# Install runtime dependencies
if (Test-Path "requirements.txt") {
    Write-Info "Installing runtime dependencies from requirements.txt..."
    & $pythonCmd -m pip install -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install runtime dependencies"
        Pop-Location
        exit 1
    }
}

# Install test dependencies
if (Test-Path "tests/requirements-test.txt") {
    Write-Info "Installing test dependencies from tests/requirements-test.txt..."
    & $pythonCmd -m pip install -q -r tests/requirements-test.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install test dependencies"
        Pop-Location
        exit 1
    }
}

Write-Success "Dependencies installed successfully"
Write-Host ""

# Run flake8 critical checks
Write-Info "========================================"
Write-Info "  Running flake8 critical checks"
Write-Info "========================================"
Write-Host ""

$flake8Args = @(
    "--select=E9,F63,F7,F82,F401,F841,W291,W292,W293",  # Critical errors + unused imports/variables + whitespace
    "extras/ace"
)

Write-Info "Flake8 command: $pythonCmd -m flake8 $($flake8Args -join ' ')"
& $pythonCmd -m flake8 @flake8Args
$flake8Exit = $LASTEXITCODE

if ($flake8Exit -eq 0) {
    Write-Success "No critical issues found"
} else {
    Write-Error "Critical issues detected - fix before running tests"
    Pop-Location
    exit $flake8Exit
}
Write-Host ""

# Set test directory
$testDir = "tests"
if (-not (Test-Path $testDir)) {
    Write-Error "ERROR: Test directory not found: $testDir"
    Pop-Location
    exit 1
}

# Build pytest command
$pytestArgs = @("-v")

if ($Verbose) {
    $pytestArgs += "-s"  # Show print statements
}

# Always run coverage for implementation files only
$pytestArgs += "--cov=ace"
$pytestArgs += "--cov-report=term-missing"
$pytestArgs += "--cov-report=html:../.tmp/htmlcov"
# Exclude test files from coverage
$pytestArgs += "--cov-config=.coveragerc"

# Ensure .tmp directory exists
New-Item -ItemType Directory -Force -Path ".tmp" | Out-Null

if ($TestFile) {
    $testPath = Join-Path $testDir $TestFile
    if (-not (Test-Path $testPath)) {
        Write-Error "ERROR: Test file not found: $testPath"
        Pop-Location
        exit 1
    }
    $pytestArgs += $testPath
    Write-Info "Running single test file: $TestFile"
} else {
    $pytestArgs += $testDir
    Write-Info "Running all tests in: $testDir"
}

Write-Host ""
Write-Info "Test command: $pythonCmd -m pytest $($pytestArgs -join ' ')"
Write-Host ""

# Run tests
$startTime = Get-Date
& $pythonCmd -m pytest @pytestArgs
$exitCode = $LASTEXITCODE
$endTime = Get-Date
$duration = $endTime - $startTime

Write-Host ""
Write-Info "========================================"
if ($exitCode -eq 0) {
    Write-Success "All tests passed!"
} else {
    Write-Error "Some tests failed"
}
Write-Info "Duration: $($duration.TotalSeconds) seconds"
Write-Info "========================================"

if ($exitCode -eq 0) {
    Write-Host ""
    Write-Info "Coverage report generated: .tmp/htmlcov/index.html"
    Write-Info "Open with: start .tmp/htmlcov/index.html"
}

Pop-Location
exit $exitCode
