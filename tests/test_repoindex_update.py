import unittest
from unittest import mock
import subprocess

import anvil
from anvil import RepoIndex, CommandExecutionError
from pathlib import Path


class TestRepoIndexUpdate(unittest.TestCase):
    def setUp(self):
        # use temp index dir to avoid touching user's real index
        import tempfile, shutil
        self._orig_index_dir = anvil.INDEX_DIR
        self._tmp = Path(tempfile.mkdtemp(prefix='anvil-index-test-'))
        anvil.INDEX_DIR = self._tmp
        self._tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        anvil.INDEX_DIR = self._orig_index_dir
        try:
            shutil.rmtree(self._tmp)
        except Exception:
            pass

    def test_update_ignores_git_pull_failure(self):
        idx = RepoIndex()
        # Ensure .git exists for the test (create a dummy dir)
        (anvil.INDEX_DIR / '.git').mkdir(parents=True, exist_ok=True)

        # Patch run_cmd to raise CommandExecutionError (simulates git pull returning non-zero)
        with mock.patch('anvil.run_cmd', side_effect=CommandExecutionError('git pull', 128, '', 'fatal: ...')), mock.patch('anvil.Colors.print') as printer:
            # Should not raise
            idx.update()
            # user-facing warning printed
            printer.assert_any_call(mock.ANY, anvil.Colors.WARNING)


if __name__ == '__main__':
    unittest.main()
