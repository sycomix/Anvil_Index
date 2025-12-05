"""Unit tests for diagnostic detection helpers.

These tests verify that the analysis helper detects MSVC runtime mismatches
and PIC-required relocation errors and returns helpful suggestions.
"""

from anvil import detect_lnk_and_pic_issues


def test_detect_lnk2038_suggestions():
    """Ensure MSVC runtime mismatch (LNK2038) produces a suggestion."""
    stderr = (
        "LNK2038: mismatch: value 'MD_DynamicRelease' "
        "doesn't match value 'MT_StaticRelease'"
    )
    suggestions = detect_lnk_and_pic_issues(stderr)
    keys = ('MSVC runtime mismatch', 'LNK2038')
    assert any(any(k in s for k in keys) for s in suggestions)


def test_detect_pic_suggestions():
    """Ensure PIC-related relocation errors suggest adding -fPIC."""
    stderr = "relocation truncated to fit: R_X86_64_32"
    suggestions = detect_lnk_and_pic_issues(stderr)
    assert any('-fPIC' in s for s in suggestions)
