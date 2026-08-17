"""Protect the source-level documentation and comment conventions."""

import ast
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parents[1] / "src"


def _source_files():
    """Return production Python modules in stable path order."""
    return sorted(SOURCE_DIR.glob("*.py"))


def _definitions(tree):
    """Yield every module, class, function, method, and nested function."""
    yield tree
    yield from (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_every_source_definition_has_a_docstring():
    """Require concise interface documentation on every source definition."""
    missing = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _definitions(tree):
            if not ast.get_docstring(node, clean=False):
                name = getattr(node, "name", "<module>")
                missing.append(f"{path.name}:{getattr(node, 'lineno', 1)}:{name}")
    assert not missing, "missing docstrings:\n" + "\n".join(missing)


def test_source_has_no_standalone_prose_expressions():
    """Reject unassigned strings that execute as comments instead of documenting an interface."""
    misplaced = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0])
            for node in _definitions(tree)
            if node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        misplaced.extend(
            f"{path.name}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and id(node) not in docstrings
        )
    assert not misplaced, "standalone string expressions:\n" + "\n".join(misplaced)


def test_source_comments_are_single_line():
    """Reject consecutive full-line comments in production modules."""
    blocks = []
    for path in _source_files():
        previous_comment = None
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            is_comment = line.lstrip().startswith("#")
            if is_comment and previous_comment == line_number - 1:
                blocks.append(f"{path.name}:{previous_comment}-{line_number}")
            previous_comment = line_number if is_comment else None
    assert not blocks, "multi-line comment blocks:\n" + "\n".join(blocks)
