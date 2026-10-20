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


@pytest.fixture(autouse=True)
def isolate_native_adapter_from_gpu_provider(monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


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


def test_unknown_location_passages_cannot_authorize_quotes():
    item = {"id": "s1", "text": "AWVALID must remain asserted.", "metadata": {"start_page": 12}}
    state = {"spec_evidence": [item]}
    assert "[spec:s1 p.12]" in agent.citable_evidence(state)
    item["metadata"] = {}
    assert agent.citable_evidence(state) == {}


@pytest.mark.parametrize("empty_lane", ["spec", "rtl"])
def test_comparison_without_a_citable_domain_skips_generation(empty_lane):
    import inspect

    calls = []

    def search(lane):
        return lambda q, k: [] if lane == empty_lane else [Document(
            page_content=lane, metadata={"chunk_id": lane, "start_page": 1,
                                         "file": "rtl/test.sv", "line": 1})]

    assert {"reranker", "lane_floors"}.isdisjoint(
        inspect.signature(agent.build_graph).parameters)
    app = agent.build_graph(search("spec"), search("rtl"),
                            lambda prompt: calls.append(prompt), [], [], {})
    state = agent.run(app, "Does the RTL implement every specified behavior?")
    assert state["query_type"] == agent.COMPARISON
    assert state["answer_status"] == "insufficient_evidence"
    assert calls == []


def test_real_graph_accepts_only_source_validated_comparison_excerpts():
    spec = Document(page_content=CATALOG["[spec:s1 p.12]"]["text"],
                    metadata={"chunk_id": "s1", "start_page": 12})
    rtl = Document(page_content=CATALOG["[rtl:cache.sv:10]"]["text"],
                   metadata={"chunk_id": "r1", "file": "cache.sv", "line": 10})
    app = agent.build_graph(lambda q, k: [spec], lambda q, k: [rtl],
                            lambda prompt: json.dumps(response()), [], [], {})
    state = agent.run(app, "Does the RTL implement AWVALID?")
    assert state["answer_status"] == "evidence_found"
    assert state["answer_validation"]["valid"]
    assert state["answer"].startswith("UNVERIFIED COMPARISON")


def test_strong_other_lane_cannot_promote_a_spec_request_into_comparison():
    spec = Document(page_content=CATALOG["[spec:s1 p.12]"]["text"],
                    metadata={"chunk_id": "s1", "start_page": 12})
    rtl = Document(page_content="assign MAWValid = pending;",
                   metadata={"chunk_id": "r1", "file": "rtl/cache.sv", "line": 10})
    app = agent.build_graph(lambda q, k: [spec], lambda q, k: [rtl],
                            lambda prompt: response(["[spec:s1 p.12]"]), [], [], {})
    state = agent.run(app, "What does AWVALID indicate?")
    assert state["query_type"] == agent.SPEC
    assert state["answer_status"] == "evidence_found"


def test_local_model_uses_a_bounded_schema_and_rejects_truncated_completion(monkeypatch):
    import httpx
    from jsonschema import Draft202012Validator

    captured = {}

    def post(url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(200, json={"message": {"content": '{"status":'},
                                        "done_reason": "length"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ValueError, match="truncated"):
        agent.local_answer("source evidence", CATALOG, {"spec", "rtl"})
    assert not Draft202012Validator(captured["format"]).is_valid({
        "status": "evidence_found", "citations": list(CATALOG) * 2})


def test_native_model_selects_sources_without_retyping_original_text(monkeypatch):
    import httpx
    from jsonschema import Draft202012Validator

    key = "[rtl:cache.sv:10]"

    def post(url, **kwargs):
        Draft202012Validator(kwargs["json"]["format"]).validate({
            "status": "evidence_found", "citations": [key]})
        return httpx.Response(200, json={"message": {"content": json.dumps({
            "status": "evidence_found", "citations": [key]})}, "done_reason": "stop"},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    canonical = agent.local_answer("source evidence", CATALOG, {"rtl"})
    assert canonical["claims"] == [{"citation": key, "quote": CATALOG[key]["text"]}]
    assert validate_answer(canonical, CATALOG, {"rtl"})[1] == []


@pytest.mark.parametrize("cuda", [False, True])
def test_native_generation_releases_unused_cuda_cache_and_unloads_after_response(monkeypatch, cuda):
    import httpx
    import torch

    events = []
    key = "[rtl:cache.sv:10]"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: events.append("release"))
    models = iter([[{"digest": agent.cfg.LOCAL_LLM_DIGEST}], []])

    def get(url, **kwargs):
        events.append("poll")
        assert url.endswith("/api/ps")
        return httpx.Response(200, json={"models": next(models)},
                              request=httpx.Request("GET", url))

    def post(url, **kwargs):
        events.append("answer")
        assert kwargs["json"]["keep_alive"] == 0
        return httpx.Response(200, json={"message": {"content": json.dumps({
            "status": "evidence_found", "citations": [key]})}, "done_reason": "stop"},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    canonical = agent.local_answer("source evidence", CATALOG, {"rtl"})
    assert canonical["status"] == "evidence_found"
    assert events == (["release", "answer", "poll", "poll"] if cuda else ["answer"])


def test_native_unload_wait_is_bounded(monkeypatch):
    import httpx

    ticks = iter([0, 11])
    monkeypatch.setattr(agent.time, "monotonic", lambda: next(ticks, 11))
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(
        200, json={"models": [{"digest": agent.cfg.LOCAL_LLM_DIGEST}]},
        request=httpx.Request("GET", url)))
    with pytest.raises(TimeoutError, match="did not unload"):
        agent._wait_for_native_unload("http://localhost:11434")


def test_native_provider_failure_still_waits_for_unload(monkeypatch):
    import httpx
    import torch

    waited = []
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(agent, "_wait_for_native_unload", lambda host: waited.append(host))

    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("provider timed out")

    monkeypatch.setattr(httpx, "post", fail)
    with pytest.raises(httpx.ReadTimeout):
        agent.local_answer("source evidence", CATALOG, {"rtl"})
    assert waited == [agent.cfg.LOCAL_LLM_HOST.rstrip("/")]


def test_native_comparison_schema_requires_both_domains_and_allows_abstention(monkeypatch):
    import httpx
    from jsonschema import Draft202012Validator

    captured = {}

    def post(url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(200, json={"message": {"content": json.dumps({
            "status": "evidence_found", "citations": list(CATALOG)})}, "done_reason": "stop"},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    spec = Document(page_content=CATALOG["[spec:s1 p.12]"]["text"],
                    metadata={"chunk_id": "s1", "start_page": 12})
    rtl = Document(page_content=CATALOG["[rtl:cache.sv:10]"]["text"],
                   metadata={"chunk_id": "r1", "file": "cache.sv", "line": 10})
    app = agent.build_graph(lambda q, k: [spec], lambda q, k: [rtl],
                            spec_records=[], spec_signals=[], bridge={})
    state = agent.run(app, "Does the RTL implement the specification's AWVALID rule?")
    assert state["answer_validation"]["valid"]
    validator = Draft202012Validator(captured["format"])
    assert validator.is_valid({"status": "evidence_found", "citations": list(CATALOG)})
    assert validator.is_valid({"status": "evidence_found", "citations": [*CATALOG, next(iter(CATALOG))]})
    assert validator.is_valid({"status": "insufficient_evidence", "citations": []})
    for key in CATALOG:
        assert not validator.is_valid({"status": "evidence_found", "citations": [key]})
    assert not validator.is_valid({"status": "evidence_found", "citations": []})
    assert not validator.is_valid({"status": "insufficient_evidence", "citations": list(CATALOG)})


def test_native_comparison_without_selectable_domain_abstains_before_generation(monkeypatch):
    import httpx

    def unexpected_call(*args, **kwargs):
        pytest.fail("a domain without selectable quotes must skip generation")

    monkeypatch.setattr(httpx, "post", unexpected_call)
    catalog = {**CATALOG, "[spec:s1 p.12]": {"domain": "spec", "text": "x" * 2001}}
    assert agent.local_answer("source evidence", catalog, {"spec", "rtl"}) == {
        "status": "insufficient_evidence", "claims": []}
