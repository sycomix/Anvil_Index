"""Unit tests for Anvil auto-submit toggle behavior."""

import os
import unittest
from anvil import Anvil

class TestAnvilAutoSubmitToggle(unittest.TestCase):
    """Verify the ANVIL_AUTO_SUBMIT environment variable toggles behavior."""
    def test_auto_submit_env_off(self):
        """ANVIL_AUTO_SUBMIT=0 disables auto submit."""
        orig = os.environ.get('ANVIL_AUTO_SUBMIT')
        try:
            os.environ['ANVIL_AUTO_SUBMIT'] = '0'
            a = Anvil()
            self.assertFalse(a.auto_submit)
        finally:
            if orig is None:
                os.environ.pop('ANVIL_AUTO_SUBMIT', None)
            else:
                os.environ['ANVIL_AUTO_SUBMIT'] = orig

    def test_auto_submit_env_on(self):
        """ANVIL_AUTO_SUBMIT=1 enables auto submit."""
        orig = os.environ.get('ANVIL_AUTO_SUBMIT')
        try:
            os.environ['ANVIL_AUTO_SUBMIT'] = '1'
            a = Anvil()
            self.assertTrue(a.auto_submit)
        finally:
            if orig is None:
                os.environ.pop('ANVIL_AUTO_SUBMIT', None)
            else:
                os.environ['ANVIL_AUTO_SUBMIT'] = orig

if __name__ == '__main__':
    unittest.main()
