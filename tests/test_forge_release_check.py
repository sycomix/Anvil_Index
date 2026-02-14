"""Unit tests for release-check behavior in Anvil.forge.

These tests patch `anvil.check_for_release` so they remain offline and
verify that `forge(..., check_release=True)` skips or performs a build
according to the helper's result.
"""
import json
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import anvil
from anvil import Anvil


def git_available():
    return shutil.which('git') is not None


class TestForgeReleaseCheck(unittest.TestCase):
    def setUp(self):
        # Create a minimal local project with an anvil.json that writes a bin file
        self.tempdir = Path(tempfile.mkdtemp(prefix='anvil-release-test-'))
        self.repo_dir = self.tempdir / 'repo'
        self.repo_dir.mkdir()
        cmd1 = (
            'python -c "import os,sys; os.makedirs(sys.argv[1] + \'/bin\', exist_ok=True)" {PREFIX}'
        )
        cmd2 = (
            'python -c "import os,sys; open(sys.argv[1] + \'/bin/mybin\', \'w\').close()" {PREFIX}'
        )
        anvil_json = {
            "name": "release-test",
            "binaries": ["mybin"],
            "build": {"common": [cmd1, cmd2]}
        }
        with open(self.repo_dir / 'anvil.json', 'w', encoding='utf-8') as f:
            json.dump(anvil_json, f)

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)
        # Cleanup any installed prefix created by tests
        try:
            shutil.rmtree(anvil.INSTALL_DIR / 'repo', ignore_errors=True)
            shutil.rmtree(anvil.INSTALL_DIR / 'release-test', ignore_errors=True)
        except Exception:
            pass

    def test_forge_skips_when_release_present(self):
        a = Anvil()
        # Patch check_for_release to indicate a platform release is present
        with mock.patch('anvil.check_for_release', return_value=True):
            a.forge(str(self.repo_dir), check_release=True)
        # Install dir should not be created by forge since release check short-circuited
        self.assertFalse((anvil.INSTALL_DIR / self.repo_dir.name).exists())

    def test_forge_builds_when_release_missing(self):
        a = Anvil()
        # Patch check_for_release to indicate no release is available; build should proceed
        with mock.patch('anvil.check_for_release', return_value=False):
            a.forge(str(self.repo_dir), check_release=True)
        install_path = anvil.INSTALL_DIR / self.repo_dir.name
        self.assertTrue(install_path.exists())
        self.assertTrue((install_path / 'bin' / 'mybin').exists())


if __name__ == '__main__':
    unittest.main()
