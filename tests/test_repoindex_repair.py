import unittest
from unittest import mock
from pathlib import Path
import os

import anvil
from anvil import RepoIndex, INDEX_REPO_URL, CommandExecutionError
from pathlib import Path


class TestRepoIndexRepair(unittest.TestCase):
    def setUp(self):
        # isolate tests from the user's real ~/.anvil/index by patching INDEX_DIR
        import tempfile, shutil
        self._orig_index_dir = anvil.INDEX_DIR
        self._tmp = Path(tempfile.mkdtemp(prefix='anvil-index-test-'))
        anvil.INDEX_DIR = self._tmp
        self._tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        # restore global and remove temp dir
        import shutil
        anvil.INDEX_DIR = self._orig_index_dir
        try:
            shutil.rmtree(self._tmp)
        except Exception:
            pass

    def test_update_reclones_when_git_dir_invalid(self):
        # create a .git file (invalid git dir)
        (anvil.INDEX_DIR / '.git').write_text('not a repo')

        # run_cmd will be called first for rev-parse (raise), then for git clone
        side_effects = [CommandExecutionError('git rev-parse', 128, '', 'fatal: not a git repo'), 'cloned']
        with mock.patch('anvil.run_cmd', side_effect=side_effects) as rc, mock.patch('anvil.Colors.print') as printer:
            idx = RepoIndex()
            idx.update()
            # ensure clone was attempted
            rc.assert_any_call(f"git clone {INDEX_REPO_URL} .", cwd=anvil.INDEX_DIR, verbose=False)
            # user-facing warning printed about automatic repair
            printer.assert_any_call(mock.ANY, anvil.Colors.WARNING)

    def test_update_clones_when_no_git_dir(self):
        # ensure .git does not exist and directory is empty
        gitp = anvil.INDEX_DIR / '.git'
        if gitp.exists():
            try:
                gitp.unlink()
            except Exception:
                pass
        with mock.patch('anvil.run_cmd', return_value='cloned') as rc:
            idx = RepoIndex()
            idx.update()
            rc.assert_any_call(f"git clone {INDEX_REPO_URL} .", cwd=anvil.INDEX_DIR, verbose=False)


if __name__ == '__main__':
    unittest.main()
