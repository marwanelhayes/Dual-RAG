"""Current Markdown guides must link to existing repository documents."""

from pathlib import Path
import re


def test_current_documentation_links_resolve():
    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("*.md"), *(root / "docs").rglob("*.md")]
    missing = []
    for path in files:
        for destination in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
            if "://" in destination or destination.startswith(("#", "/", "mailto:")):
                continue
            target = destination.partition("#")[0]
            if target and not (path.parent / target).exists():
                missing.append(f"{path.relative_to(root)}: {destination}")
    assert not missing, "broken documentation links:\n" + "\n".join(missing)
