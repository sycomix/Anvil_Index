"""Run mypy against the codebase (helper used by CI / devs).

Usage: python scripts/check_types.py
"""
import subprocess
import sys

if __name__ == '__main__':
    cmd = [sys.executable, '-m', 'mypy', 'anvil.py']
    rc = subprocess.call(cmd)
    sys.exit(rc)
