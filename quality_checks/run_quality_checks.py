#!/usr/bin/env python3
"""
Comprehensive Quality Check Script for Just-EdTech Project

This script runs formatting and linting using Ruff and Black with progress tracking,
automatic fixing, and detailed reporting.

Defaults:
    - Auto-fix is enabled by default. The script will attempt to fix all fixable issues.

Usage:
    python run_quality_checks.py                 # Default: auto-fix + final checks
    python run_quality_checks.py --auto-fix      # Explicitly enable auto-fix (default)
    python run_quality_checks.py --check-only    # Checks only (no modifications)
    python run_quality_checks.py --no-fix        # Format only (no Ruff auto-fixes), then checks
"""

import subprocess
import sys
import time
from pathlib import Path


def print_header():
    """Print script header."""
    print("=" * 70)
    print("🔍 JUST-EDTECH COMPREHENSIVE QUALITY CHECK SCRIPT")
    print("=" * 70)
    print(f"⏰ Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


def print_progress(step: int, total: int, description: str, status: str = "RUNNING"):
    """Print progress with step counter."""
    status_icons = {
        "RUNNING": "🔄",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARNING": "⚠️",
        "FIXED": "🔧",
        "INFO": "ℹ️",
    }

    icon = status_icons.get(status, "📋")
    print(f"\n[{step}/{total}] {icon} {description}")
    print("-" * 60)


def run_command(
    command: list[str],
    description: str,
    step: int,
    total: int,
    show_output: bool = True,
) -> tuple[bool, str]:
    """Run a command and return success status and output."""
    try:
        print(f"Running: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=False)

        if result.returncode == 0:
            print_progress(step, total, f"{description} - PASSED", "SUCCESS")
            if show_output and result.stdout and result.stdout.strip():
                print(f"Output: {result.stdout.strip()}")
            return True, result.stdout
        else:
            print_progress(step, total, f"{description} - FAILED", "ERROR")
            if result.stderr:
                print(f"Error: {result.stderr}")
            return False, result.stderr

    except Exception as e:
        print_progress(step, total, f"{description} - ERROR", "ERROR")
        print(f"Exception: {str(e)}")
        return False, str(e)


def print_summary(results: list[tuple[str, bool]], start_time: float):
    """Print final summary."""
    end_time = time.time()
    duration = end_time - start_time

    print("\n" + "=" * 70)
    print("📊 QUALITY CHECK SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, success in results if success)
    total = len(results)

    print(f"✅ Passed: {passed}/{total}")
    print(f"❌ Failed: {total - passed}/{total}")
    print(f"⏱️  Duration: {duration:.2f} seconds")

    print("\n📋 Detailed Results:")
    for description, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} - {description}")

    if passed == total:
        print("\n🎉 All quality checks passed!")
        return True
    else:
        print(f"\n⚠️  {total - passed} quality check(s) failed.")
        print("\n💡 Suggestions:")
        print("  - Run with auto-fix: python run_quality_checks.py")
        print("  - Check specific issues: poetry run ruff check . --output-format=text")
        print("  - Format code: poetry run black .")
        return False


def main():
    """Main function."""
    start_time = time.time()
    print_header()

    # Check if we're in the right directory
    if not Path("pyproject.toml").exists():
        print(
            "❌ Error: pyproject.toml not found. Please run this script from the project root."
        )
        sys.exit(1)

    # Check if poetry is available
    try:
        subprocess.run(["poetry", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Error: Poetry not found. Please install Poetry first.")
        sys.exit(1)

    # Parse command line arguments (simple parsing, no argparse dependency)
    def has_flag(flag):
        return any(arg == flag for arg in sys.argv[1:])

    check_only = has_flag("--check-only")
    # --auto-fix is the default; presence doesn't change behavior, so we don't store it
    explicit_no_fix = has_flag("--no-fix")

    # Determine mode
    if check_only:
        mode = "check_only"
        print("🔍 Running in CHECK-ONLY mode (no modifications)")
        total_checks = 2
    elif explicit_no_fix:
        mode = "no_fix"
        print("🧹 Running in FORMAT-ONLY mode (no Ruff auto-fixes)")
        # ruff format, black, ruff check, black --check
        total_checks = 4
    else:
        # Default and --auto-fix both map to auto-fix mode
        mode = "auto_fix"
        print("🔧 Running with AUTO-FIX enabled (Ruff --fix + formatting)")
        # ruff fix, ruff format, black, ruff check, black --check
        total_checks = 5

    results = []

    if mode == "check_only":
        # Run only checks, no formatting or fixes
        step = 1
        success, _ = run_command(
            ["poetry", "run", "ruff", "check", "."],
            "Ruff Linting Check",
            step,
            total_checks,
            show_output=False,
        )
        results.append(("Ruff Linting Check", success))

        step = 2
        success, _ = run_command(
            ["poetry", "run", "black", ".", "--check"],
            "Black Formatting Check",
            step,
            total_checks,
            show_output=False,
        )
        results.append(("Black Formatting Check", success))

    elif mode == "no_fix":
        # Format only (no Ruff auto-fix), then run checks
        step = 1
        success, _ = run_command(
            ["poetry", "run", "ruff", "format", "."],
            "Ruff Code Formatting",
            step,
            total_checks,
        )
        results.append(("Ruff Code Formatting", success))

        step = 2
        success, _ = run_command(
            ["poetry", "run", "black", "."],
            "Black Code Formatting",
            step,
            total_checks,
        )
        results.append(("Black Code Formatting", success))

        step = 3
        success, _ = run_command(
            ["poetry", "run", "ruff", "check", "."],
            "Ruff Linting Check",
            step,
            total_checks,
            show_output=False,
        )
        results.append(("Ruff Linting Check", success))

        step = 4
        success, _ = run_command(
            ["poetry", "run", "black", ".", "--check"],
            "Black Formatting Check",
            step,
            total_checks,
            show_output=False,
        )
        results.append(("Black Formatting Check", success))

    else:  # mode == "auto_fix"
        # Auto-fix all fixable issues first, then format, then final checks
        step = 1
        success, _ = run_command(
            ["poetry", "run", "ruff", "check", ".", "--fix", "--unsafe-fixes"],
            "Ruff Auto-Fix (with unsafe fixes)",
            step,
            total_checks,
        )
        results.append(("Ruff Auto-Fix", success))

        step = 2
        success, _ = run_command(
            ["poetry", "run", "ruff", "format", "."],
            "Ruff Code Formatting",
            step,
            total_checks,
        )
        results.append(("Ruff Code Formatting", success))

        step = 3
        success, _ = run_command(
            ["poetry", "run", "black", "."],
            "Black Code Formatting",
            step,
            total_checks,
        )
        results.append(("Black Code Formatting", success))

        step = 4
        success, _ = run_command(
            ["poetry", "run", "ruff", "check", "."],
            "Ruff Linting Check",
            step,
            total_checks,
            show_output=False,
        )
        results.append(("Ruff Linting Check", success))

        step = 5
        success, _ = run_command(
            ["poetry", "run", "black", ".", "--check"],
            "Black Formatting Check",
            step,
            total_checks,
            show_output=False,
        )
        results.append(("Black Formatting Check", success))

    # Print summary and exit
    success = print_summary(results, start_time)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
