#!/usr/bin/env python3
"""
Syntax validation script for ACEPRO Python files.
Compiles all Python files to detect syntax errors before deployment.
"""

import py_compile
import glob
import sys
import os

# ANSI color codes
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color


def print_header():
    """Print script header."""
    print("=" * 70)
    print(f"{BLUE}ACEPRO Python Syntax Validation{NC}")
    print("=" * 70)
    print()


def compile_file(filepath):
    """
    Attempt to compile a Python file.
    
    Returns:
        tuple: (success: bool, error_msg: str or None)
    """
    try:
        py_compile.compile(filepath, doraise=True)
        return True, None
    except py_compile.PyCompileError as e:
        # Extract error message
        error_msg = str(e)
        return False, error_msg
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def main():
    """Main validation logic."""
    print_header()
    
    # Change to repo root (parent of script directory)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    os.chdir(repo_root)
    
    print(f"Working directory: {repo_root}")
    print()
    
    # Find all Python files
    extras_files = sorted(glob.glob('extras/**/*.py', recursive=True))
    test_files = sorted(glob.glob('tests/**/*.py', recursive=True))
    all_files = extras_files + test_files
    
    print(f"{BLUE}Checking {len(extras_files)} files in extras/{NC}")
    print(f"{BLUE}Checking {len(test_files)} files in tests/{NC}")
    print(f"{BLUE}Total: {len(all_files)} Python files{NC}")
    print()
    
    # Track errors
    errors = []
    
    # Compile each file
    for idx, filepath in enumerate(all_files, 1):
        success, error_msg = compile_file(filepath)
        
        if not success:
            errors.append((filepath, error_msg))
            print(f"[{idx}/{len(all_files)}] {RED}✗{NC} {filepath}")
        else:
            # Only show progress for successful files every 10 files
            if idx % 10 == 0 or idx == len(all_files):
                print(f"[{idx}/{len(all_files)}] {GREEN}✓{NC} Validated {idx} files...")
    
    print()
    print("=" * 70)
    
    if errors:
        print(f"{RED}❌ FOUND {len(errors)} SYNTAX ERROR(S):{NC}")
        print("=" * 70)
        print()
        
        for filepath, error_msg in errors:
            print(f"{RED}File: {filepath}{NC}")
            print(f"  {error_msg}")
            print()
        
        print(f"{YELLOW}Fix the above errors before deploying to Klipper.{NC}")
        sys.exit(1)
    else:
        print(f"{GREEN}✓✓✓ ALL {len(all_files)} FILES COMPILE SUCCESSFULLY ✓✓✓{NC}")
        print("=" * 70)
        print()
        print(f"{GREEN}✓ No syntax errors detected{NC}")
        print(f"{GREEN}✓ No multi-line f-string issues{NC}")
        print(f"{GREEN}✓ Ready for Klipper deployment{NC}")
        sys.exit(0)


if __name__ == '__main__':
    main()
