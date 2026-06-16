#!/usr/bin/env python3
"""
Simple Quality Check Script for Just-EdTech Project

This script runs code quality checks using Ruff and Black with progress tracking.
"""

import subprocess
import sys
import time
from pathlib import Path


def print_header():
    """Print script header."""
    print("=" * 60)
    print("🔍 JUST-EDTECH QUALITY CHECK SCRIPT")
    print("=" * 60)
    print(f"⏰ Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


def print_progress(step: int, total: int, description: str, status: str = "RUNNING"):
    """Print progress with step counter."""
    status_icons = {
        "RUNNING": "🔄",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARNING": "⚠️",
        "FIXED": "🔧",
    }

    icon = status_icons.get(status, "📋")
    print(f"\n[{step}/{total}] {icon} {description}")
    print("-" * 50)


def run_command(command: list[str], description: str, step: int, total: int) -> bool:
    """Run a command and return success status."""
    try:
        print(f"Running: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=False)

        if result.returncode == 0:
            print_progress(step, total, f"{description} - PASSED", "SUCCESS")
            if result.stdout and result.stdout.strip():
                print(f"Output: {result.stdout.strip()}")
            return True
        else:
            print_progress(step, total, f"{description} - FAILED", "ERROR")
            if result.stderr:
                print(f"Error: {result.stderr}")
            return False

    except Exception as e:
        print_progress(step, total, f"{description} - ERROR", "ERROR")
        print(f"Exception: {str(e)}")
        return False


def print_summary(results: list[tuple[str, bool]], start_time: float):
    """Print final summary."""
    end_time = time.time()
    duration = end_time - start_time

    print("\n" + "=" * 60)
    print("📊 QUALITY CHECK SUMMARY")
    print("=" * 60)

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
        print(
            f"\n⚠️  {total - passed} quality check(s) failed. Please review the output above."
        )
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

    # Parse command line arguments
    auto_fix = "--no-fix" not in sys.argv
    check_only = "--check-only" in sys.argv

    if check_only:
        print("🔍 Running in CHECK-ONLY mode (no auto-fixing)")
        total_checks = 2
    elif auto_fix:
        print("🔧 Running with AUTO-FIX enabled")
        total_checks = 6
    else:
        print("🔍 Running without auto-fix")
        total_checks = 4

    results = []

    if check_only:
        # Run only checks, no formatting
        step = 1
        success = run_command(
            ["poetry", "run", "ruff", "check", "."],
            "Ruff Linting Check",
            step,
            total_checks,
        )
        results.append(("Ruff Linting Check", success))

        step = 2
        success = run_command(
            ["poetry", "run", "black", ".", "--check"],
            "Black Formatting Check",
            step,
            total_checks,
        )
        results.append(("Black Formatting Check", success))

    else:
        # Step 1: Ruff linting check
        step = 1
        success = run_command(
            ["poetry", "run", "ruff", "check", "."],
            "Ruff Linting Check",
            step,
            total_checks,
        )
        results.append(("Ruff Linting Check", success))

        # Step 2: Ruff formatting
        step = 2
        success = run_command(
            ["poetry", "run", "ruff", "format", "."],
            "Ruff Code Formatting",
            step,
            total_checks,
        )
        results.append(("Ruff Code Formatting", success))

        # Step 3: Black formatting check
        step = 3
        success = run_command(
            ["poetry", "run", "black", ".", "--check"],
            "Black Formatting Check",
            step,
            total_checks,
        )
        results.append(("Black Formatting Check", success))

        # Step 4: Black formatting
        step = 4
        success = run_command(
            ["poetry", "run", "black", "."], "Black Code Formatting", step, total_checks
        )
        results.append(("Black Code Formatting", success))

        # Additional steps if auto_fix is enabled
        if auto_fix:
            # Step 5: Ruff fix
            step = 5
            success = run_command(
                ["poetry", "run", "ruff", "check", ".", "--fix"],
                "Ruff Auto-Fix",
                step,
                total_checks,
            )
            results.append(("Ruff Auto-Fix", success))

            # Step 6: Final Black format
            step = 6
            success = run_command(
                ["poetry", "run", "black", "."],
                "Final Black Format",
                step,
                total_checks,
            )
            results.append(("Final Black Format", success))

    # Print summary and exit
    success = print_summary(results, start_time)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
