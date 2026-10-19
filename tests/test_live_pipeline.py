"""Optional integration checks use the real retrieval models, tokenizers and answer model."""

import os
import json
import time

import pytest

import agent
import configure as cfg


pytestmark = [pytest.mark.live_model,
              pytest.mark.skipif(os.environ.get("CHIPVER_LIVE_TESTS") != "1",
                                 reason="set CHIPVER_LIVE_TESTS=1 with a built corpus and Ollama")]


def test_real_runtime_returns_only_validated_excerpts():
    runtime = agent.Runtime()
    runtime.preflight()
    app = agent.build_graph(runtime=runtime)
    outcomes = []
    for question in ("What does the RISC-V specification permit for WFI?",
                     "Where is risc_mux2 implemented in the RTL?",
                     "Does the RTL implement the specification's AWVALID handshake rule?"):
        started = time.perf_counter()
        state = agent.run(app, question)
        outcomes.append({"question": question, "status": state["answer_status"],
                         "valid": state["answer_validation"]["valid"],
                         "claims": len(state["answer_validation"]["claims"]),
                         "seconds": time.perf_counter() - started})
        (cfg.OUTPUT_DIR / "live_smoke.json").write_text(json.dumps(outcomes, indent=2))
        assert state["answer_status"] in {"evidence_found", "insufficient_evidence"}, state["answer_validation"]
        assert state["answer_validation"]["valid"]
        for claim in state["answer_validation"]["claims"]:
            assert claim["citation"] in state["citable_evidence"]
    assert any(row["status"] == "evidence_found" for row in outcomes)
