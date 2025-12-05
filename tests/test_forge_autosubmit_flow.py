"""Integration tests for Anvil's forge and auto-submit behavior.

These tests verify that repositories are automatically submitted to the
local index (and a PR link provided) when `ANVIL_AUTO_SUBMIT` is enabled,
and that behavior can be disabled via the env var.
"""

import os
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anvil import Anvil


def git_available():
    return shutil.which('git') is not None


class TestForgeAutoSubmitFlow(unittest.TestCase):
    """Integration test class for forge + auto submit behavior."""
    def setUp(self):
        """Create a temporary git repo with a minimal anvil.json formula."""
        if not git_available():
            self.skipTest('git is required for this integration test')
        self.tempdir = Path(tempfile.mkdtemp(prefix='anvil-test-'))
        self.repo_dir = self.tempdir / 'repo'
        self.repo_dir.mkdir()
        # Create minimal repo with anvil.json build step that creates a bin file
        cmd1 = (
            'python -c "import os,sys; '
            'os.makedirs(sys.argv[1] + \'/bin\', exist_ok=True)" {PREFIX}'
        )
        cmd2 = (
            'python -c "import os,sys; '
            'open(sys.argv[1] + \'/bin/mybin\', \'w\').close()" {PREFIX}'
        )
        anvil_json = {
            "name": "autosubmit-test",
            "binaries": ["mybin"],
            "build": {
                "common": [
                    cmd1,
                    cmd2,
                ]
            }
        }
        # Write the anvil.json describing two short build steps to be executed
        with open(self.repo_dir / 'anvil.json', 'w', encoding='utf-8') as f:
            json.dump(anvil_json, f)
        # init git repo and set remote
        subprocess.check_call(['git', 'init'], cwd=str(self.repo_dir))
        subprocess.check_call(['git', 'config', 'user.email', 'test@example.com'], cwd=str(self.repo_dir))
        subprocess.check_call(['git', 'config', 'user.name', 'Test User'], cwd=str(self.repo_dir))
        subprocess.check_call(['git', 'add', '.'], cwd=str(self.repo_dir))
        subprocess.check_call(['git', 'commit', '-m', 'initial'], cwd=str(self.repo_dir))
        remote = 'https://github.com/testuser/autosubmit-test.git'
        subprocess.check_call(['git', 'remote', 'add', 'origin', remote], cwd=str(self.repo_dir))

    def tearDown(self):
        """Remove temporary directories created in setUp."""
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_auto_submit_enabled(self):
        """When ANVIL_AUTO_SUBMIT=1 the repo should be added to the local index."""
        orig = os.environ.get('ANVIL_AUTO_SUBMIT')
        try:
            os.environ['ANVIL_AUTO_SUBMIT'] = '1'
            a = Anvil()
            # Use a temporary index DB for test isolation
            tmp_db = Path(tempfile.gettempdir()) / 'anvil_test_index_integration.db'
            if tmp_db.exists():
                tmp_db.unlink()
            a.index.db_path = tmp_db
            a.index.ensure_db()
            # Forge local path
            a.forge(str(self.repo_dir))
            self.assertTrue(a.index.has_url('https://github.com/testuser/autosubmit-test'))
        finally:
            if orig is None:
                os.environ.pop('ANVIL_AUTO_SUBMIT', None)
            else:
                os.environ['ANVIL_AUTO_SUBMIT'] = orig

    def test_auto_submit_disabled(self):
        """When ANVIL_AUTO_SUBMIT=0 the repo should not be added to the local index."""
        orig = os.environ.get('ANVIL_AUTO_SUBMIT')
        try:
            os.environ['ANVIL_AUTO_SUBMIT'] = '0'
            a = Anvil()
            tmp_db = Path(tempfile.gettempdir()) / 'anvil_test_index_integration2.db'
            if tmp_db.exists():
                tmp_db.unlink()
            a.index.db_path = tmp_db
            a.index.ensure_db()
            a.forge(str(self.repo_dir))
            self.assertFalse(a.index.has_url('https://github.com/testuser/autosubmit-test'))
        finally:
            if orig is None:
                os.environ.pop('ANVIL_AUTO_SUBMIT', None)
            else:
                os.environ['ANVIL_AUTO_SUBMIT'] = orig


if __name__ == '__main__':
    unittest.main()
