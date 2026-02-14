import os
import shutil
import tempfile
import unittest
from pathlib import Path

from anvil import Anvil, ANVIL_ROOT, BUILD_DIR, BIN_DIR, INSTALL_DIR


class TestHousekeepingSafety(unittest.TestCase):
    def setUp(self):
        # Ensure directories exist for test isolation
        ANVIL_ROOT.mkdir(parents=True, exist_ok=True)
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        # Create a sentinel file in ANVIL_ROOT that must not be removed
        (ANVIL_ROOT / 'DO_NOT_DELETE').write_text('keep')

        # Create junk files inside build dir
        (BUILD_DIR / 'junk1').write_text('x')
        sub = BUILD_DIR / 'subdir'
        sub.mkdir(exist_ok=True)
        (sub / 'junk2').write_text('y')

        # Create a fake installed package and a binary shim
        pkg = INSTALL_DIR / 'pkg1'
        pkg.mkdir(parents=True, exist_ok=True)
        (BIN_DIR / 'pkg1').write_text('shim')
        # orphaned binary (should be removed)
        (BIN_DIR / 'oldbin').write_text('old')

    def tearDown(self):
        try:
            shutil.rmtree(ANVIL_ROOT)
        except OSError:
            pass

    def test_housekeeping_preserves_anvil_root_and_cleans_build(self):
        a = Anvil()
        # run housekeeping
        a.housekeeping()
        # sentinel in ANVIL_ROOT must remain
        self.assertTrue((ANVIL_ROOT / 'DO_NOT_DELETE').exists())
        # build dir itself must still exist, but its contents removed
        self.assertTrue(BUILD_DIR.exists())
        self.assertEqual(list(BUILD_DIR.iterdir()), [])

    def test_housekeeping_removes_orphaned_binaries_only(self):
        a = Anvil()
        a.housekeeping()
        # 'pkg1' binary should remain because package installed
        self.assertTrue((BIN_DIR / 'pkg1').exists())
        # 'oldbin' should be removed
        self.assertFalse((BIN_DIR / 'oldbin').exists())


if __name__ == '__main__':
    unittest.main()
