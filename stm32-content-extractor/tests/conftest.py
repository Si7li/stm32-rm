"""Shared fixtures.

The end-to-end tests need a real ST reference manual. They look for
RM0490 in the repository's `usermanuel/` folder (matched by glob, not by
a hardcoded filename beyond the RM number) and skip cleanly when it is
not present, so the unit tests still run on a checkout without the PDFs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANUALS_DIR = REPO_ROOT / "usermanuel"


def find_manual(rm_number: str) -> Path | None:
    if not MANUALS_DIR.is_dir():
        return None
    matches = sorted(MANUALS_DIR.glob(f"{rm_number.lower()}-*.pdf"))
    return matches[0] if matches else None


@pytest.fixture(scope="session")
def rm0490_path() -> Path:
    path = find_manual("rm0490")
    if path is None:
        pytest.skip("RM0490 PDF not available in usermanuel/")
    return path


class FakeContents:
    """Stands in for `contents.ContentsIndex` in unit tests."""

    def __init__(self, chapters: dict | None = None, sections: dict | None = None):
        self.chapters = chapters or {}
        self.sections = sections or {}

    def chapter_title(self, chapter: str) -> str:
        entry = self.chapters.get(chapter)
        return entry[0] if entry else ""


@pytest.fixture
def fake_contents():
    return FakeContents
