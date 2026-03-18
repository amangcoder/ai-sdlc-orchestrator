#!/usr/bin/env python3
"""Test runner script."""
import subprocess
import sys

r = subprocess.run(
    [sys.executable, '-m', 'pytest', 'tests/integration/', '--tb=short', '-q'],
    cwd='/Users/amangupta/Projects/orchestrator-for-mobile-ui',
    capture_output=True,
    text=True
)
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[-2000:])
print("Return code:", r.returncode)
