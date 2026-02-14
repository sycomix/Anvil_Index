import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Patterns to forbid in production code (scan .py files excluding tests/ and __pycache__)
BANNED_PATTERNS = [
    (re.compile(r"except\s+Exception\s*:"), "broad-except"),
    (re.compile(r"\bTODO\b", re.IGNORECASE), "todo-comment"),
    (re.compile(r"\bFIXME\b", re.IGNORECASE), "fixme-comment"),
]

EXCLUDE_DIRS = {"tests", "__pycache__", ".git"}


def iter_production_py_files(root: Path):
    for p in root.rglob("*.py"):
        rel = p.relative_to(root)
        parts = rel.parts
        if parts and parts[0] in EXCLUDE_DIRS:
            continue
        yield p


class TestAiRulesCompliance(unittest.TestCase):
    def test_no_banned_patterns_in_production(self):
        violations = []
        for f in iter_production_py_files(ROOT):
            try:
                txt = f.read_text(encoding="utf-8")
            except OSError:
                continue
            for pattern, name in BANNED_PATTERNS:
                if pattern.search(txt):
                    violations.append((str(f.relative_to(ROOT)), name))
        if violations:
            msgs = [f"{p}: {rule}" for p, rule in violations]
            self.fail("ai-rules violations found:\n" + "\n".join(msgs))


if __name__ == '__main__':
    unittest.main()
