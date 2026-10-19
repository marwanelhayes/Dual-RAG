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


import re
import json

import pytest


@pytest.fixture(autouse=True)
def _identifier_signal_tagger(monkeypatch, request):
    """Stand in for the GLiNER2 signal tagger: every identifier word in a text is a tag."""
    if request.node.get_closest_marker("live_model"):
        return
    import agent
    import ingest

    monkeypatch.setattr(agent, "answer_tokens", lambda text: len(re.findall(r"\w+|[^\w\s]", text)))

    monkeypatch.setattr(ingest, "_tokenizer", lambda: lambda text, **kwargs: {
        "input_ids": re.findall(r"\w+|[^\w\s]", text)})

    def tag(texts):
        return {word.casefold(): 0.9 for text in texts
                for word in re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", str(text))}

    monkeypatch.setattr(agent, "tag_signal_names", tag)


@pytest.fixture
def routing_vocabulary(tmp_path, monkeypatch):
    """Supply explicit design-only identifiers without relying on a private corpus build."""
    import agent
    import configure as cfg

    names = ["ForwardAE", "hazard_unit", "csr_file", "opcode_decoder"]
    rtl = {"modules": {"fixture": {"ports": [{"original_name": name} for name in names]}}}
    path = tmp_path / "rtl.json"
    path.write_text(json.dumps(rtl))
    signals = tmp_path / "signals.json"
    signals.write_text('{"signals": []}')
    monkeypatch.setattr(cfg, "RTL_PATH", path)
    monkeypatch.setattr(cfg, "SPEC_SIGNALS_PATH", signals)
    agent._design_names.cache_clear()
    yield
    agent._design_names.cache_clear()
