"""Tests for build environment helper that forces MSVC runtime selection."""

import os
import unittest

from anvil import default_build_env


class TestBuildEnv(unittest.TestCase):
    """Validate build environment settings on Windows vs other platforms."""

    def test_default_build_env_windows(self):
        """Verify default environment uses dynamic CRT on Windows and not on other OSes."""
        env = default_build_env()
        if os.name == 'nt':
            # On Windows we expect CL to include /MD for dynamic CRT and CMake var set
            self.assertIn('/MD', env.get('CL', ''))
            self.assertEqual(env.get('CMAKE_MSVC_RUNTIME_LIBRARY'), 'MultiThreadedDLL')
        else:
            # On non-Windows we do not expect these vars to be set
            self.assertNotIn('CMAKE_MSVC_RUNTIME_LIBRARY', env)

    def test_override_msvc_runtime(self):
        """Test that ANVIL_MSVC_RUNTIME=MT forces static runtime selection (MT)."""
        orig = os.environ.get('ANVIL_MSVC_RUNTIME')
        try:
            os.environ['ANVIL_MSVC_RUNTIME'] = 'MT'
            env = default_build_env()
            if os.name == 'nt':
                self.assertIn('/MT', env.get('CL', ''))
                self.assertEqual(env.get('CMAKE_MSVC_RUNTIME_LIBRARY'), 'MultiThreaded')
            else:
                self.assertNotIn('CMAKE_MSVC_RUNTIME_LIBRARY', env)
        finally:
            if orig is None:
                os.environ.pop('ANVIL_MSVC_RUNTIME', None)
            else:
                os.environ['ANVIL_MSVC_RUNTIME'] = orig

    def test_force_pic_on_posix(self):
        """Ensure that ANVIL_FORCE_PIC adds -fPIC to CFLAGS/CXXFLAGS on POSIX."""
        orig = os.environ.get('ANVIL_FORCE_PIC')
        try:
            os.environ['ANVIL_FORCE_PIC'] = '1'
            env = default_build_env()
            if os.name != 'nt':
                self.assertIn('-fPIC', env.get('CFLAGS', '') + env.get('CXXFLAGS', ''))
            else:
                # On Windows, ANVIL_FORCE_PIC may be present in env, but CFLAGS/CXXFLAGS should not have -fPIC
                self.assertNotIn('-fPIC', env.get('CFLAGS', '') + env.get('CXXFLAGS', ''))
        finally:
            if orig is None:
                os.environ.pop('ANVIL_FORCE_PIC', None)
            else:
                os.environ['ANVIL_FORCE_PIC'] = orig

    def test_param_override_msvc_runtime(self):
        """Passing msvc_runtime_override param to default_build_env sets MSVC runtime flags."""
        env = default_build_env(msvc_runtime_override='MT')
        if os.name == 'nt':
            self.assertIn('/MT', env.get('CL', ''))
            self.assertEqual(env.get('CMAKE_MSVC_RUNTIME_LIBRARY'), 'MultiThreaded')
        else:
            self.assertNotIn('CMAKE_MSVC_RUNTIME_LIBRARY', env)


if __name__ == '__main__':
    unittest.main()

