#!/usr/bin/env python3
"""
Quality Check Script for Just-EdTech Project

This script runs comprehensive code quality checks using Ruff and Black,
with progress tracking and automatic fixing capabilities.
"""

import subprocess
import sys
import time
from pathlib import Path


class QualityChecker:
    """Handles code quality checks with progress tracking."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.start_time = time.time()
        self.total_checks = 6  # Total number of checks to run
        self.current_check = 0

    def print_header(self):
        """Print script header."""
        print("=" * 60)
        print("🔍 JUST-EDTECH QUALITY CHECK SCRIPT")
        print("=" * 60)
        print(f"📁 Project Root: {self.project_root}")
        print(f"⏰ Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

    def print_progress(self, step: str, status: str = "RUNNING"):
        """Print progress with step counter."""
        self.current_check += 1
        progress = f"[{self.current_check}/{self.total_checks}]"

        status_icons = {
            "RUNNING": "🔄",
            "SUCCESS": "✅",
            "ERROR": "❌",
            "WARNING": "⚠️",
            "FIXED": "🔧",
        }

        icon = status_icons.get(status, "📋")
        print(f"\n{progress} {icon} {step}")
        print("-" * 50)

    def run_command(
        self, command: list[str], description: str, fixable: bool = False
    ) -> tuple[bool, str]:
        """Run a command and return success status and output."""
        try:
            print(f"Running: {' '.join(command)}")
            result = subprocess.run(
                command,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                self.print_progress(f"{description} - PASSED", "SUCCESS")
                if result.stdout:
                    print(f"Output: {result.stdout.strip()}")
                return True, result.stdout
            else:
                if fixable and "ruff" in command[1]:
                    # Try to fix with ruff --fix
                    fix_command = command.copy()
                    if "check" in fix_command:
                        fix_command[fix_command.index("check")] = "check"
                        fix_command.append("--fix")
                    else:
                        fix_command.append("--fix")

                    print(f"🔧 Attempting to fix issues with: {' '.join(fix_command)}")
                    fix_result = subprocess.run(
                        fix_command,
                        cwd=self.project_root,
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    if fix_result.returncode == 0:
                        self.print_progress(f"{description} - FIXED", "FIXED")
                        return True, fix_result.stdout
                    else:
                        self.print_progress(
                            f"{description} - FAILED (fix attempted)", "ERROR"
                        )
                        print(f"Error: {fix_result.stderr}")
                        return False, fix_result.stderr
                else:
                    self.print_progress(f"{description} - FAILED", "ERROR")
                    print(f"Error: {result.stderr}")
                    return False, result.stderr

        except Exception as e:
            self.print_progress(f"{description} - ERROR", "ERROR")
            print(f"Exception: {str(e)}")
            return False, str(e)

    def check_ruff_linting(self) -> bool:
        """Check code with Ruff linting."""
        success, _ = self.run_command(
            ["poetry", "run", "ruff", "check", "."], "Ruff Linting Check", fixable=True
        )
        return success

    def format_with_ruff(self) -> bool:
        """Format code with Ruff."""
        success, _ = self.run_command(
            ["poetry", "run", "ruff", "format", "."], "Ruff Code Formatting"
        )
        return success

    def check_black_formatting(self) -> bool:
        """Check formatting with Black."""
        success, _ = self.run_command(
            ["poetry", "run", "black", ".", "--check"],
            "Black Formatting Check",
            fixable=True,
        )
        return success

    def format_with_black(self) -> bool:
        """Format code with Black."""
        success, _ = self.run_command(
            ["poetry", "run", "black", "."], "Black Code Formatting"
        )
        return success

    def run_ruff_fix(self) -> bool:
        """Run Ruff with --fix to automatically fix issues."""
        success, _ = self.run_command(
            ["poetry", "run", "ruff", "check", ".", "--fix"], "Ruff Auto-Fix"
        )
        return success

    def run_black_format(self) -> bool:
        """Run Black formatting."""
        success, _ = self.run_command(
            ["poetry", "run", "black", "."], "Black Auto-Format"
        )
        return success

    def print_summary(self, results: list[tuple[str, bool]]):
        """Print final summary."""
        end_time = time.time()
        duration = end_time - self.start_time

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

    def run_all_checks(self, auto_fix: bool = True) -> bool:
        """Run all quality checks."""
        self.print_header()

        results = []

        # Step 1: Ruff linting check
        ruff_check_success = self.check_ruff_linting()
        results.append(("Ruff Linting Check", ruff_check_success))

        # Step 2: Ruff formatting
        ruff_format_success = self.format_with_ruff()
        results.append(("Ruff Code Formatting", ruff_format_success))

        # Step 3: Black formatting check
        black_check_success = self.check_black_formatting()
        results.append(("Black Formatting Check", black_check_success))

        # Step 4: Black formatting
        black_format_success = self.format_with_black()
        results.append(("Black Code Formatting", black_format_success))

        # Step 5: Additional Ruff fix if auto_fix is enabled
        if auto_fix:
            ruff_fix_success = self.run_ruff_fix()
            results.append(("Ruff Auto-Fix", ruff_fix_success))

        # Step 6: Final Black format
        final_black_success = self.run_black_format()
        results.append(("Final Black Format", final_black_success))

        return self.print_summary(results)


def main():
    """Main function."""
    project_root = Path(__file__).parent.absolute()

    # Check if we're in the right directory
    if not (project_root / "pyproject.toml").exists():
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
    elif auto_fix:
        print("🔧 Running with AUTO-FIX enabled")
    else:
        print("🔍 Running without auto-fix")

    # Run quality checks
    checker = QualityChecker(project_root)

    if check_only:
        # Run only checks, no formatting
        checker.total_checks = 2
        results = []

        ruff_check_success = checker.check_ruff_linting()
        results.append(("Ruff Linting Check", ruff_check_success))

        black_check_success = checker.check_black_formatting()
        results.append(("Black Formatting Check", black_check_success))

        success = checker.print_summary(results)
    else:
        success = checker.run_all_checks(auto_fix=auto_fix)

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
