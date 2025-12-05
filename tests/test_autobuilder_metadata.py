"""Tests for AutoBuilder metadata detection and build environment overrides.

Ensures `anvil.json` metadata such as 'msvc_runtime' and 'force_pic' are
detected and honored by the build environment helper.
"""

import json
import tempfile
import shutil
import os
from pathlib import Path

from anvil import AutoBuilder, default_build_env


def test_autobuilder_detects_metadata_and_build_env():
    """Detects metadata and ensures default_build_env respects overrides."""
    tmpdir = Path(tempfile.mkdtemp(prefix='anvil-test-meta-'))
    try:
        # Write a minimal anvil.json specifying metadata
        data = {
            "name": "meta-test",
            "binaries": [],
            "msvc_runtime": "MT",
            "force_pic": True,
            "build": {"common": []}
        }
        with open(tmpdir / 'anvil.json', 'w', encoding='utf-8') as f:
            json.dump(data, f)

            _, _, metadata = AutoBuilder.detect(tmpdir, tmpdir / 'install')
        assert metadata['msvc_runtime'] == 'MT'
        assert metadata['force_pic'] is True

        # Ensure default_build_env respects metadata override
        env = default_build_env(
            msvc_runtime_override=metadata['msvc_runtime'],
            force_pic_override=metadata['force_pic']
        )
        if os.name == 'nt':
            assert '/MT' in env.get('CL', '')
        else:
            assert '-fPIC' in (env.get('CFLAGS', '') + env.get('CXXFLAGS', ''))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
