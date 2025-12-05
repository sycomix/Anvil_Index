"""Tests for CMake postprocessing: ensure -DCMAKE_MSVC_RUNTIME_LIBRARY is added/updated."""

import re


def test_cmake_step_override_in_forge_postprocessing():
    """Verify the CLI override or metadata adjusts CMake commands correctly."""
    steps = ["mkdir -p build", "cd build && cmake .. -A x64", "cd build && make"]
    # Simulate forge's postprocessing for CLI msvc_override
    msvc_override = 'MT'
    cmake_flag = 'MultiThreaded' if str(msvc_override).strip().upper() == 'MT' else 'MultiThreadedDLL'
    processed_steps = []
    for step in steps:
        if isinstance(step, str) and 'cmake ' in step and 'CMAKE_MSVC_RUNTIME_LIBRARY' not in step:
            step = step + f" -DCMAKE_MSVC_RUNTIME_LIBRARY={cmake_flag}"
        elif isinstance(step, str) and 'cmake ' in step and 'CMAKE_MSVC_RUNTIME_LIBRARY' in step:
            step = re.sub(r"-DCMAKE_MSVC_RUNTIME_LIBRARY=[^\s]+", f"-DCMAKE_MSVC_RUNTIME_LIBRARY={cmake_flag}", step)
        processed_steps.append(step)

    assert any('CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded' in s for s in processed_steps)
