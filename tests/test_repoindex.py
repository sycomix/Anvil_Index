"""Unit tests for RepoIndex normalization and DB helpers."""

import tempfile
from pathlib import Path
import unittest

from anvil import RepoIndex

class TestRepoIndexNormalization(unittest.TestCase):
    """Tests for URL normalization and index add/lookup behavior."""
    def setUp(self):
        # Use a temporary db file so tests do not touch user's actual index
        self.tmp_db = Path(tempfile.gettempdir()) / 'anvil_test_index.db'
        if self.tmp_db.exists():
            try:
                self.tmp_db.unlink()
            except OSError:
                pass
        self.index = RepoIndex()
        # override the db path to our temporary file
        self.index.db_path = self.tmp_db
        self.index.ensure_db()

    def tearDown(self):
        if self.tmp_db.exists():
            try:
                self.tmp_db.unlink()
            except OSError:
                pass

    def test_normalize_url_git_ssh(self):
        url = 'git@github.com:projectdiscovery/katana.git'
        normalized = RepoIndex.normalize_url(url)
        self.assertEqual(normalized, 'https://github.com/projectdiscovery/katana')

    def test_normalize_url_http(self):
        url = 'https://github.com/ProjectDiscovery/Katana.git'
        normalized = RepoIndex.normalize_url(url)
        self.assertEqual(normalized, 'https://github.com/projectdiscovery/katana')

    def test_add_local_and_has_url(self):
        url_ssh = 'git@github.com:projectdiscovery/katana.git'
        name = 'katana'
        self.index.add_local(name, url_ssh)
        self.assertTrue(self.index.has_url('https://github.com/projectdiscovery/katana'))
        self.assertTrue(self.index.has_url('git@github.com:projectdiscovery/katana.git'))

if __name__ == '__main__':
    unittest.main()
