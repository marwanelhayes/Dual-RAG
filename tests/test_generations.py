"""A failed rebuild must never replace the corpus a running reader uses."""

from pathlib import Path

import pytest

import artifacts
import build


def test_publishing_keeps_existing_readers_on_their_generation(tmp_path):
    first = tmp_path / "generations/first"
    second = tmp_path / "generations/second"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "value").write_text("old")
    (second / "value").write_text("new")
    artifacts.publish_generation(tmp_path, first)
    pinned = (tmp_path / "current").resolve()
    artifacts.publish_generation(tmp_path, second)
    assert (pinned / "value").read_text() == "old"
    assert ((tmp_path / "current").resolve() / "value").read_text() == "new"


def test_generation_integrity_detects_changed_artifacts(tmp_path):
    (tmp_path / "chunks.jsonl").write_text("original\n")
    artifacts.write_generation_manifest(tmp_path, inputs={}, metadata={})
    artifacts.verify_generation(tmp_path)
    (tmp_path / "chunks.jsonl").write_text("changed\n")
    with pytest.raises(ValueError, match="changed"):
        artifacts.verify_generation(tmp_path)


def test_failed_rebuild_leaves_the_previous_generation_active(tmp_path, monkeypatch):
    root = tmp_path / "output"
    previous = root / "generations/previous"
    previous.mkdir(parents=True)
    artifacts.publish_generation(root, previous)
    monkeypatch.setattr(build.cfg, "ROOT", tmp_path)
    monkeypatch.setattr(build.cfg, "OUTPUT_ROOT", root)
    monkeypatch.setattr(artifacts, "source_hashes", lambda: {})
    monkeypatch.setattr(artifacts, "build_metadata", lambda: {})

    def failed_stage(*args):
        raise RuntimeError("stage failed")

    monkeypatch.setattr(build, "run_pipeline", failed_stage)
    with pytest.raises(RuntimeError, match="stage failed"):
        build.build_generation()
    assert (root / "current").resolve() == previous
