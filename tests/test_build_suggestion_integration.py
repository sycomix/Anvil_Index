"""Integration tests for build-time suggestion detection.

This module verifies that when a build step fails with linker/runtime
errors (e.g., MSVC LNK2038 or missing -fPIC issues), Anvil prints
useful suggestions to help fix the build.
"""

import tempfile
import shutil
from pathlib import Path
import json
from unittest.mock import patch
from anvil import Anvil, CommandExecutionError


def test_build_error_suggestion_integration():
    """Ensure Anvil prints suggestion messages when builds fail with linker errors."""
    tempdir = Path(tempfile.mkdtemp(prefix='anvil-suggest-'))
    try:
        # Minimal repo with an anvil.json that triggers a single command step
        repo = tempdir / 'repo'
        repo.mkdir()
        data = {
            "name": "suggest-test",
            "binaries": [],
            "build": {"common": ["do-something"]}
        }
        with open(repo / 'anvil.json', 'w', encoding='utf-8') as f:
            json.dump(data, f)

        # Monkeypatch run_cmd to raise CommandExecutionError with a LNK2038-like stderr
        def fake_run_cmd(*args, **kwargs):
            stderr_msg = (
                "LNK2038: mismatch: value 'MD_DynamicRelease' "
                "doesn't match value 'MT_StaticRelease'"
            )
            # `run_cmd` passes the command string as the first positional arg
            cmd = args[0] if args else kwargs.get('command', 'cmd')
            raise CommandExecutionError(cmd, 1, "", stderr_msg)

        with patch('anvil.run_cmd', side_effect=fake_run_cmd):
            # Capture Colors.print outputs
            printed = []
            def fake_print(*args):
                # Colors.print may be called with (msg) or (msg, color); capture first param
                if args:
                    printed.append(str(args[0]))
            with patch('anvil.Colors.print', new=fake_print):
                a = Anvil()
                try:
                    a.forge(str(repo))
                except CommandExecutionError:
                    # After the exception, we should have suggestions printed
                    keys = ('MSVC runtime mismatch', 'LNK2038', '-fPIC')
                    suggestions = [p for p in printed if any(k in p for k in keys)]
                    assert len(suggestions) > 0
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)
