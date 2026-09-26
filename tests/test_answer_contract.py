"""Source integrity and comparison-intent contracts, independent of model behavior."""

import json

import pytest

import agent
from answer_contract import validate_answer, render_answer
from langchain_core.documents import Document


CATALOG = {
    "[spec:s1 p.12]": {"domain": "spec", "text": "AWVALID must remain asserted."},
    "[rtl:cache.sv:10]": {"domain": "rtl", "text": "assign MAWValid = pending;"},
}


def response(citations=tuple(CATALOG)):
    return {"status": "evidence_found", "claims": [
        {"citation": key, "quote": CATALOG[key]["text"]} for key in citations]}


def test_valid_source_excerpts_never_become_a_behavioral_verdict():
    data, errors = validate_answer(json.dumps(response()), CATALOG, {"spec", "rtl"})
    assert not errors
    answer = render_answer(data, comparison=True)
    assert answer.startswith("UNVERIFIED COMPARISON")
    assert "assign MAWValid = pending;" in answer


@pytest.mark.parametrize("mutate, error", [
    (lambda data: data.update(status="implemented"), "unsupported_verdict"),
    (lambda data: data["claims"][0].update(citation="[spec:s1 p.99]"), "invalid_citation"),
    (lambda data: data["claims"][1].update(citation="[rtl:cache.sv:11]"), "invalid_citation"),
    (lambda data: data["claims"][0].update(quote="AWVALID need not remain asserted."), "quote_not_in_evidence"),
    (lambda data: data["claims"][0].update(text="Fully compliant"), "unsupported_claim"),
])
def test_untrusted_model_output_fails_closed(mutate, error):
    data = response()
    mutate(data)
    assert validate_answer(data, CATALOG, {"spec", "rtl"}) == (None, [error])


def test_comparison_requires_quotes_from_both_domains():
    assert validate_answer(response(["[spec:s1 p.12]"]), CATALOG, {"spec", "rtl"})[1] == [
        "missing_domain_citations"]


def test_truncated_or_unknown_location_passages_cannot_authorize_quotes():
    item = {"id": "s1", "text": "AWVALID must remain asserted.", "metadata": {"start_page": 12}}
    state = {"spec_evidence": [item]}
    context = agent._spec_context([item], [])
    assert "[spec:s1 p.12]" in agent.citable_evidence(state, context)
    assert agent.citable_evidence(state, context[:-5]) == {}
    item["metadata"] = {}
    assert agent.citable_evidence(state, agent._spec_context([item], [])) == {}


@pytest.mark.parametrize("weak_lane", ["spec", "rtl"])
def test_comparison_intent_survives_one_weak_lane_and_skips_generation(weak_lane):
    calls = []

    class Scorer:
        def score_pairs(self, pairs):
            return [-9 if weak_lane in text else 1 for _, text in pairs]

    def doc(lane):
        return Document(page_content=lane, metadata={"chunk_id": lane, "start_page": 1,
                                                    "file": "rtl/test.sv", "line": 1})

    app = agent.build_graph(lambda q, k: [doc("spec")], lambda q, k: [doc("rtl")],
                            lambda prompt: calls.append(prompt), [], {"sections": []}, [],
                            {"modules": {}}, {}, reranker=Scorer(),
                            lane_floors={"spec": -6, "rtl": -6})
    state = agent.run(app, "Does the RTL implement every specified behavior?")
    assert state["query_type"] == agent.COMPARISON
    assert state["answer_status"] == "insufficient_evidence"
    assert calls == []


def test_real_graph_accepts_only_source_validated_comparison_excerpts():
    class Scorer:
        def score_pairs(self, pairs):
            return [1 for _ in pairs]

    spec = Document(page_content=CATALOG["[spec:s1 p.12]"]["text"],
                    metadata={"chunk_id": "s1", "start_page": 12})
    rtl = Document(page_content=CATALOG["[rtl:cache.sv:10]"]["text"],
                   metadata={"chunk_id": "r1", "file": "rtl/cache.sv", "line": 10})
    app = agent.build_graph(lambda q, k: [spec], lambda q, k: [rtl],
                            lambda prompt: json.dumps(response()), [], {"sections": []}, [],
                            {"modules": {}}, {}, reranker=Scorer(),
                            lane_floors={"spec": -6, "rtl": -6})
    state = agent.run(app, "Does the RTL implement AWVALID?")
    assert state["answer_status"] == "evidence_found"
    assert state["answer_validation"]["valid"]
    assert state["answer"].startswith("UNVERIFIED COMPARISON")


def test_strong_other_lane_cannot_promote_a_spec_request_into_comparison():
    class Scorer:
        def score_pairs(self, pairs):
            return [1 for _ in pairs]

    spec = Document(page_content=CATALOG["[spec:s1 p.12]"]["text"],
                    metadata={"chunk_id": "s1", "start_page": 12})
    rtl = Document(page_content="assign MAWValid = pending;",
                   metadata={"chunk_id": "r1", "file": "rtl/cache.sv", "line": 10})
    app = agent.build_graph(lambda q, k: [spec], lambda q, k: [rtl],
                            lambda prompt: response(["[spec:s1 p.12]"]), [], {"sections": []}, [],
                            {"modules": {}}, {}, reranker=Scorer(),
                            lane_floors={"spec": -6, "rtl": -6})
    state = agent.run(app, "What does AWVALID indicate?")
    assert state["query_type"] == agent.SPEC
    assert state["answer_status"] == "evidence_found"
