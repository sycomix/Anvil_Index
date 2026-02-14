import sys
import unittest
from unittest import mock
from pathlib import Path

import anvil


class TestCliIndexCommands(unittest.TestCase):
    def setUp(self):
        # isolate index dir
        import tempfile, shutil
        self._orig_index_dir = anvil.INDEX_DIR
        self._tmp = Path(tempfile.mkdtemp(prefix='anvil-index-cli-'))
        anvil.INDEX_DIR = self._tmp
        self._tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        anvil.INDEX_DIR = self._orig_index_dir
        try:
            shutil.rmtree(self._tmp)
        except Exception:
            pass

    def test_index_repair_cli_invokes_repair(self):
        with mock.patch.object(anvil.RepoIndex, 'repair') as mock_repair:
            with mock.patch.object(sys, 'argv', ['anvil.py', 'index', 'repair']):
                anvil.main()
                mock_repair.assert_called_once()

    def test_index_check_cli_reports_issues(self):
        # ensure index has stray files and no .git
        (anvil.INDEX_DIR / 'index.db').write_text('corrupt')
        with mock.patch('anvil.Colors.print') as printer:
            with mock.patch.object(sys, 'argv', ['anvil.py', 'index', 'check']):
                anvil.main()
                # should print a warning that index has issues
                printer.assert_any_call(mock.ANY, anvil.Colors.WARNING)


if __name__ == '__main__':
    unittest.main()
