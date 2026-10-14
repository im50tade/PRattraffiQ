"""Gemeinsame Testeinstellungen: Review-Datenbank und Befragungsablage in temporären Dateien."""

import os
import tempfile
from pathlib import Path

_ABLAGE = Path(tempfile.mkdtemp(prefix="pr_tests_"))
os.environ.setdefault("PR_REVIEW_DB", str(_ABLAGE / "review.sqlite"))
