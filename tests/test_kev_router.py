"""Kev's external response boundary and the actual conditional retrieval paths."""

import httpx
import pytest

import agent
import configure as cfg
from kev_router import CRITERIA, KevRouter


def _response(body):
    return httpx.Response(200, json=body, request=httpx.Request("POST", "http://kev"))


def _answer(choice="SPEC", probabilities=None):
    return {"model": "kev-latest", "answers": {"route": {
        "type": "choice", "choice": choice,
        "probabilities": probabilities or {"SPEC": 0.9, "RTL": 0.05, "COMPARISON": 0.05}}}}


def _server(monkeypatch, body):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response({"models": [{
        "name": "kev-latest", "run": cfg.KEV_RUN, "truncate_states": False}]}))
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return _response(body)
    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_real_system_one_shape_and_classification_payload(monkeypatch):
    calls = _server(monkeypatch, _answer())
    result = KevRouter()("What is AWVALID?")
    assert result["query_type"] == "SPEC"
    assert result["fallback"] is None
    assert calls[0]["state"] == "What is AWVALID?"
    assert calls[0]["questions"]["route"]["criteria"] == CRITERIA


def test_uncertain_route_uses_both_and_preserves_raw_choice(monkeypatch):
    _server(monkeypatch, _answer("RTL", {"SPEC": 0.3, "RTL": 0.4, "COMPARISON": 0.3}))
    result = KevRouter()("What does this do?")
    assert result["query_type"] == "COMPARISON"
    assert result["selected"] == "RTL"
    assert result["fallback"] == "low_probability"


@pytest.mark.parametrize("body", [
    {}, [], {"truncated": True},
    _answer("OTHER"), _answer("RTL"),
    _answer(probabilities={"SPEC": 0.9, "RTL": 0.1}),
    _answer(probabilities={"SPEC": 0.9, "RTL": 0.1, "COMPARISON": 0.1}),
    _answer(probabilities={"SPEC": True, "RTL": 0, "COMPARISON": 0}),
    _answer(probabilities={"SPEC": float("nan"), "RTL": 0, "COMPARISON": 0}),
])
def test_invalid_model_responses_never_skip_a_domain(monkeypatch, body):
    _server(monkeypatch, {})
    # Inject decoded JSON so NaN is checked even though strict JSON encoders refuse it.
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return body
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: Response())
    result = KevRouter()("Does this meet the spec?")
    assert result["query_type"] == "COMPARISON"
    assert result["fallback"].startswith("router_error:")


def test_timeout_and_wrong_checkpoint_fall_back_explicitly(monkeypatch):
    calls = _server(monkeypatch, _answer())
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response({"models": []}))
    assert KevRouter()("x")["fallback"] == "router_error:ValueError"
    assert calls == []
    router = KevRouter()
    router.model_info = {"run": cfg.KEV_RUN}
    def timeout(*a, **kw):
        raise httpx.ReadTimeout("test")
    monkeypatch.setattr(httpx, "post", timeout)
    assert router("x")["fallback"] == "router_error:ReadTimeout"


@pytest.mark.parametrize("route,expected", [
    ("SPEC", ["spec"]), ("RTL", ["rtl"]), ("COMPARISON", ["spec", "rtl"]),
])
def test_conditional_graph_skips_unselected_retriever_and_resets_state(route, expected):
    calls = []
    decisions = iter([route, "SPEC"])
    runtime = agent.Runtime(
        spec_retriever=lambda q, k: calls.append("spec") or [],
        rtl_retriever=lambda q, k: calls.append("rtl") or [],
        answer_model=lambda context: pytest.fail("no evidence must not call answer model"),
        spec_records=[], spec_signals=[], bridge={}, retrieval_mode="kev",
        router=lambda question: {"query_type": next(decisions), "source": "kev"})
    graph = agent.build_graph(runtime=runtime)
    # Wording intentionally disagrees: Kev alone chooses the graph path.
    state = agent.run(graph, "Does the RTL satisfy every specification requirement?")
    assert calls == expected
    assert state["query_type"] == route
    assert ("bridge_lookup" in {e["event"] for e in state["trace"]}) == (route == "COMPARISON")
    calls.clear()
    second = agent.run(graph, "Where is the RTL?")
    assert calls == ["spec"]
    assert not second.get("rtl_evidence")


def test_comparison_still_requires_both_domains():
    from langchain_core.documents import Document
    called = []
    runtime = agent.Runtime(
        spec_retriever=lambda q, k: [Document(page_content="shall", metadata={
            "chunk_id": "s1", "source_file": "spec.pdf", "start_page": 1})],
        rtl_retriever=lambda q, k: [], answer_model=lambda context: called.append(context),
        spec_records=[], spec_signals=[], bridge={}, retrieval_mode="kev",
        router=lambda question: {"query_type": "COMPARISON", "source": "kev"})
    state = agent.run(agent.build_graph(runtime=runtime), "Does it conform?")
    assert called == []
    assert state["answer_status"] == "insufficient_evidence"


def test_unknown_retrieval_mode_is_an_error():
    with pytest.raises(ValueError, match="retrieval mode"):
        agent.Runtime(retrieval_mode="kevv")
