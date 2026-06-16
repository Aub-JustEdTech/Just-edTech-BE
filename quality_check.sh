#!/bin/bash
# Simple shell script wrapper for quality checks

echo "🔍 Running Just-EdTech Quality Checks..."
echo "========================================"

# Check if we're in the right directory
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Error: pyproject.toml not found. Please run this script from the project root."
    exit 1
fi

# Check if poetry is available
if ! command -v poetry &> /dev/null; then
    echo "❌ Error: Poetry not found. Please install Poetry first."
    exit 1
fi

# Run the Python quality check script (auto-fix defaults are handled in Python)
python3 quality_checks/run_quality_checks.py "$@"
