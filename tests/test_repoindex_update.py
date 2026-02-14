import unittest
from unittest import mock
import subprocess

import anvil
from anvil import RepoIndex, CommandExecutionError


class TestRepoIndexUpdate(unittest.TestCase):
    def test_update_ignores_git_pull_failure(self):
        idx = RepoIndex()
        # Ensure .git exists for the test (create a dummy dir)
        (anvil.INDEX_DIR / '.git').mkdir(parents=True, exist_ok=True)

        # Patch run_cmd to raise CommandExecutionError (simulates git pull returning non-zero)
        with mock.patch('anvil.run_cmd', side_effect=CommandExecutionError('git pull', 128, '', 'fatal: ...')):
            # Should not raise
            idx.update()

        # cleanup
        try:
            (anvil.INDEX_DIR / '.git').rmdir()
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main()
