"""Safety and ordering tests for the one-command Dual-RAG rebuild."""

from pathlib import Path

import build
from embed import build_manifest, chunk_fingerprint


def test_stage_order_builds_each_domain_before_the_bridge():
    names = [path.name for _, path in build.stage_scripts(include_embeddings=True)]

    assert names == [
        "ingest.py", "spec_signals.py", "embed.py", "rtl_ingest.py",
        "rtl_embed.py", "bridge.py",
    ]


def test_parser_only_order_skips_both_indexes():
    names = [path.name for _, path in build.stage_scripts(include_embeddings=False)]

    assert names == ["ingest.py", "spec_signals.py", "rtl_ingest.py", "bridge.py"]


def test_full_build_enables_bridge_semantic_reranking_only_after_indexes_exist():
    scripts = build.stage_scripts(include_embeddings=True)
    commands = [build.stage_command(path, include_embeddings=True) for _, path in scripts]

    assert commands[-1][-2:] == [str(scripts[-1][1]), "--rerank"]
    assert all("--rerank" not in command for command in commands[:-1])


def test_obsolete_cleanup_is_exact_and_preserves_current_artifacts(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    (output / "kg_graph.json").write_text("old")
    (output / "spec_hierarchy.json").write_text("current")
    monkeypatch.setattr(build.cfg, "ROOT", tmp_path)
    monkeypatch.setattr(build.cfg, "OUTPUT_DIR", output)

    removed = build.remove_obsolete_artifacts()

    assert removed == [output / "kg_graph.json"]
    assert (output / "spec_hierarchy.json").read_text() == "current"


def test_clean_refuses_a_project_root_target(tmp_path, monkeypatch):
    monkeypatch.setattr(build.cfg, "ROOT", tmp_path)
    monkeypatch.setattr(build.cfg, "OUTPUT_DIR", Path(tmp_path))

    try:
        build.clean_generated_data()
    except RuntimeError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("unsafe output path was accepted")


def test_pipeline_invokes_isolated_stages_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(build, "remove_obsolete_artifacts", lambda: [])
    monkeypatch.setattr(
        build.subprocess, "run",
        lambda command, cwd, check: calls.append((command[2], cwd, check)))

    build.run_pipeline(include_embeddings=False)

    assert [Path(command).name for command, _, _ in calls] == [
        "ingest.py", "spec_signals.py", "rtl_ingest.py", "bridge.py",
    ]
    assert all(cwd == build.cfg.ROOT and check for _, cwd, check in calls)


def test_index_manifest_invalidates_persisted_metadata_without_reembedding_contract_drift():
    first = {"chunk_id": "c", "context_header": "Spec > Section", "text": "same",
             "section_id": "old"}
    second = {**first, "section_id": "new"}

    assert chunk_fingerprint(first) == chunk_fingerprint(second)
    assert build_manifest([first]) != build_manifest([second])


def test_no_stage_imports_a_later_stage():
    """Pipeline stages depend only on stages that run before them."""
    import ast

    order = [path for _, path in build.stage_scripts(include_embeddings=True)]
    stems = [path.stem for path in order]
    for index, path in enumerate(order):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                     for alias in node.names}
        assert not imported & set(stems[index + 1:]), path.name
