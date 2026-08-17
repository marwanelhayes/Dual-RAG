"""Shared test setup that makes script-style ``src`` modules importable.

The pipeline modules are written to be run as scripts from the repo root, so they
import each other by bare name (`import configure as cfg`). Tests get the same view by
putting src/ on sys.path here rather than by turning the project into a package.
"""

import sys
from pathlib import Path

# Match the import view used by ``python src/<stage>.py``.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
