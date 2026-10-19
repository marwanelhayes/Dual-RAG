"""Hermetic contracts for the three-route Dual-RAG LangGraph."""

import json
import re

from langchain_core.documents import Document

import agent
import configure as cfg


def test_repeated_stale_bridge_loads_always_fail(monkeypatch):
    import bridge
    import pytest

    monkeypatch.setattr(bridge, "load_bridge", lambda path: {"source_fingerprints": {"id": "old"}})
    monkeypatch.setattr(bridge, "source_fingerprints", lambda: {"id": "new"})
    runtime = agent.Runtime()
    for _ in range(2):
        with pytest.raises(agent.ArtifactFreshnessError, match="stale"):
            runtime.get_bridge()


def test_simultaneous_cold_searches_initialize_one_retriever(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    import retrieve
    import embed

    loads = []

    def load(**kwargs):
        loads.append(1)
        time.sleep(0.02)
        return lambda question, k: []

    monkeypatch.setattr(embed, "index_manifest_status", lambda *args: {"fresh": True})
    monkeypatch.setattr(retrieve.Retriever, "load", load)
    runtime = agent.Runtime(spec_records=[])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda question: runtime.spec_search(question, 1), ["one", "two"]))
    assert len(loads) == 1


def test_context_budget_removes_whole_passages_and_their_citation_permission(monkeypatch):
    monkeypatch.setattr(agent, "answer_tokens", lambda text: len(text))
    monkeypatch.setattr(cfg, "AGENT_NUM_CTX", 1500)
    monkeypatch.setattr(cfg, "ANSWER_MAX_NEW_TOKENS", 100)
    documents = [_doc(f"s{index}", "body " * 100, start_page=index + 1) for index in range(4)]
    app = agent.build_graph(lambda q, k: documents, lambda q, k: [],
                            lambda prompt: {"status": "insufficient_evidence", "claims": []},
                            [], [], {})
    state = agent.run(app, "What does the specification state?")
    assert len(state["final_context"]) <= 1400
    assert 0 < len(state["spec_evidence"]) < 4
    assert all(item["text"] == "body " * 100 for item in state["spec_evidence"])
    assert len(state["citable_evidence"]) == len(state["spec_evidence"])


def _doc(chunk_id, text, **metadata):
    return Document(page_content=text, metadata={"chunk_id": chunk_id, **metadata})


def _runtime_data():
    records = [{
        "chunk_id": "spec#1", "section_id": "sec:fpu", "subsection_id": "sec:round",
        "context_header": "Spec > FPU > Rounding", "text": "Rounding mode selects RNE.",
        "start_page": 12,
    }]
    signals = [{
        "signal_id": "specsig:rounding_mode", "name": "rounding_mode",
        "normalized_name": "rounding mode", "width": 3, "direction": "input",
        "section_id": "sec:fpu", "subsection_id": "sec:round", "chunk_id": "spec#1",
        "table_id": "tbl:1",
    }]
    bridge = {
        "signal_alignments": [{
            "spec_signal_id": "specsig:rounding_mode",
            "spec_original_name": "rounding_mode",
            "rtl_signal_id": "rtl::signal::fpu::rm_i",
            "rtl_original_name": "rm_i", "rtl_module_id": "rtl::mod::fpu",
            "rtl_module_name": "fpu", "confidence": 0.96,
            "confidence_level": "STRONG",
        }],
        "unresolved_signals": [],
        "module_alignments": [{
            "spec_section_id": "sec:fpu",
            "spec_section_title": "Floating Point Unit",
            "rtl_module_id": "rtl::mod::fpu", "rtl_module_name": "fpu",
            "confidence": 0.91, "confidence_level": "STRONG",
        }],
    }
    return records, signals, bridge


def test_classifier_exposes_exactly_three_routes():
    assert agent.QUERY_TYPES == {agent.SPEC, agent.RTL, agent.COMPARISON}
    assert agent.classify_query("What rounding modes are required?") == agent.SPEC
    assert agent.classify_query("Where does the specification define AWVALID?") == \
        agent.SPEC
    assert agent.classify_query("Where is rm_i driven in the RTL?") == agent.RTL
    assert agent.classify_query("Does the RTL implement every rounding mode?") == \
        agent.COMPARISON


def test_both_lanes_retrieve_for_every_question_whatever_the_wording():
    calls = []
    records, signals, bridge = _runtime_data()

    def spec_search(query, k):
        calls.append("spec")
        return [_doc("spec#1", "Rounding is required.", section_id="sec:fpu",
                     subsection_id="sec:round", start_page=12)]

    def rtl_search(query, k):
        calls.append("rtl")
        return [_doc("rtl:fpu", "module fpu", module="fpu", file="rtl/fpu.sv", line=1)]

    app = agent.build_graph(
        spec_search, rtl_search, lambda prompt: "answer", records,
        signals, bridge)
    state = agent.run(app, "What rounding modes are required?")

    assert calls == ["spec", "rtl"]
    assert [event["event"] for event in state["trace"]] == [
        "route_prior", "spec_retrieval", "rtl_retrieval", "bridge_lookup",
        "evidence_merge", "answer",
    ]


def test_requested_route_is_separate_from_observed_evidence():
    records, signals, bridge = _runtime_data()
    app = agent.build_graph(
        lambda query, k: [_doc("spec#1", "unrelated prose", section_id="sec:fpu")],
        lambda query, k: [_doc("rtl:fpu", "module fpu", module="fpu",
                               file="rtl/fpu.sv", line=1)],
        lambda prompt: "answer", records, signals, bridge)

    state = agent.run(app, "What causes a stall?")

    assert state["query_type"] == agent.SPEC


def test_comparison_label_needs_both_lanes_above_the_floor():
    records, signals, bridge = _runtime_data()
    app = agent.build_graph(
        lambda query, k: [_doc("spec#1", "Rounding modes are required.",
                               section_id="sec:fpu", subsection_id="sec:round",
                               start_page=12)],
        lambda query, k: [_doc("rtl:fpu", "module fpu", module="fpu",
                               file="rtl/fpu.sv", line=1)],
        lambda prompt: "implemented with cited evidence",
        records, signals, bridge)

    state = agent.run(app, "Does the RTL implement every rounding mode?")

    assert state["query_type"] == agent.COMPARISON
    assert state["answer_status"] == "rejected"
    assert state["answer"].startswith("INSUFFICIENT EVIDENCE")


def test_model_is_refused_only_when_no_lane_cleared_the_floor():
    model_calls = []
    records, signals, bridge = _runtime_data()
    app = agent.build_graph(
        lambda query, k: [], lambda query, k: [],
        lambda prompt: model_calls.append(prompt) or "unsafe",
        records, signals, bridge=bridge)

    state = agent.run(app, "Does the RTL implement every rounding mode?")

    assert model_calls == []
    assert state["answer"].startswith("INSUFFICIENT EVIDENCE")
    assert state["trace"][-1] == {"event": "answer", "model_called": False}


def test_signal_selection_follows_names_the_tagger_finds(monkeypatch):
    monkeypatch.setattr(agent, "tag_signal_names", lambda texts: {"rounding_mode": 0.8})
    signals = [
        {"signal_id": "a", "name": "rounding_mode", "chunk_id": "spec#1"},
        {"signal_id": "b", "name": "unrelated", "chunk_id": "spec#1"},
    ]
    evidence = [{"id": "spec#1", "text": "rounding_mode selects RNE", "metadata": {}}]

    selected = agent.select_spec_signals("rounding behavior", evidence, signals)

    assert [item["signal_id"] for item in selected] == ["a"]


def test_signal_selection_orders_by_tag_confidence_and_keeps_one_row_per_name(monkeypatch):
    monkeypatch.setattr(agent, "tag_signal_names",
                        lambda texts: {"awvalid": 0.6, "wvalid": 0.9})
    signals = [{"signal_id": "aw1", "name": "AWVALID"}, {"signal_id": "w", "name": "WVALID"},
               {"signal_id": "aw2", "name": "AWVALID"}]

    selected = agent.select_spec_signals("q", [], signals)

    assert [item["signal_id"] for item in selected] == ["w", "aw1"]


def test_tagging_keeps_identifier_shaped_signal_and_register_spans(monkeypatch):
    class FakeModel:
        def batch_extract_entities(self, texts, types, **kwargs):
            return [{"entities": {
                "signal": [{"text": "AWVALID", "confidence": 0.97},
                           {"text": "Valid-Ready signals", "confidence": 0.81}],
                "register": [{"text": "mstatus", "confidence": 0.7},
                             {"text": "AWVALID", "confidence": 0.5}],
            }} for _ in texts]

    monkeypatch.undo()
    monkeypatch.setattr(agent, "_signal_tagger", lambda: FakeModel())

    assert agent.tag_signal_names(["one", "two"]) == {"awvalid": 0.97, "mstatus": 0.7}


def test_empty_spec_selection_does_not_return_unrelated_bridge_rows():
    artifact = {"signal_alignments": [{
        "spec_signal_id": "unrelated", "spec_original_name": "unrelated",
        "rtl_signal_id": "rtl::unrelated", "rtl_original_name": "unrelated_i",
        "confidence_level": "STRONG",
    }], "unresolved_signals": [], "module_alignments": [{
        "spec_section_id": "elsewhere", "rtl_module_id": "rtl::mod::elsewhere",
        "rtl_module_name": "elsewhere", "confidence_level": "STRONG",
    }]}

    matches, modules = agent.lookup_bridge([], [], artifact)

    assert matches == [] and modules == []


def test_merged_context_keeps_every_passage_whole_and_separates_domains():
    state = {
        "question": "Does this match?", "query_type": agent.COMPARISON,
        "spec_evidence": [{"id": f"s{i}", "text": str(i) * 5000,
                           "metadata": {"start_page": 1}} for i in range(2)],
        "spec_signals": [], "bridge_matches": [], "module_matches": [],
        "rtl_evidence": [{"id": "r", "text": "r" * 5000,
                          "metadata": {"file": "r.sv", "line": 2}}],
    }

    context = agent.merge_evidence(state)

    assert all(text * 5000 in context for text in ("0", "1", "r"))
    assert "SPEC EVIDENCE" in context and "RTL EVIDENCE" in context
    assert "[TRUNCATED]" not in context


def test_trace_report_exposes_query_type_and_structured_events():
    report = agent.trace_report({
        "query_type": agent.COMPARISON,
        "trace": [{"event": "spec_retrieval", "chunks": 3}],
        "final_context": "bounded evidence",
    })

    assert '"query_type": "COMPARISON"' in report
    assert '"chunks": 3' in report
    assert '"final_context": "bounded evidence"' in report


def test_alignment_rows_state_how_the_identity_was_found_not_a_verdict():
    """Name the link's method so it cannot read as a behavioural verdict."""
    matches = [{"spec_original_name": "AWVALID", "rtl_original_name": "MAWValid",
                "confidence_level": "MEDIUM", "method": "interface_prefix"}]

    rendered = agent._bridge_context(matches, [])

    assert "identity_match=MEDIUM" in rendered
    assert "method=interface_prefix" in rendered
    assert "confidence=" not in rendered


def test_query_recovery_is_gone():
    import inspect

    import retrieve

    for name in ("fuse_recovery", "recovery_query", "local_query"):
        assert not hasattr(agent, name)
    assert not hasattr(retrieve, "reciprocal_rank_fusion")
    assert "query_recovery" not in inspect.signature(agent.build_graph).parameters


def test_dead_agent_helpers_are_gone():
    for name in ("observed_route", "ask", "drop_near_duplicates", "_shingles"):
        assert not hasattr(agent, name)


def test_spec_evidence_is_the_retrieved_passages_only():
    records = [{"chunk_id": f"doc#0010#{index:03d}", "hierarchy_id": "section",
                "section_id": "section", "chunk_index": index, "text": str(index),
                "context_header": "Section"} for index in range(3)]
    app = agent.build_graph(
        spec_retriever=lambda query, k: [_doc("doc#0010#001", "1", section_id="section",
                                              chunk_index=1)],
        rtl_retriever=lambda query, k: [], answer_model=lambda prompt: "answer",
        spec_records=records, spec_signals=[], bridge={})

    state = agent.run(app, "What rounding modes are required?")

    assert [item["id"] for item in state["spec_evidence"]] == ["doc#0010#001"]


def test_rtl_evidence_is_the_retrieved_records_only():
    records, signals, bridge = _runtime_data()
    app = agent.build_graph(
        spec_retriever=lambda query, k: [_doc("spec#1", "Rounding mode selects RNE.",
                                              section_id="sec:fpu", start_page=12)],
        rtl_retriever=lambda query, k: [], answer_model=lambda prompt: "answer",
        spec_records=records, spec_signals=signals, bridge=bridge)

    state = agent.run(app, "Does fpu implement rounding_mode?")

    assert state["bridge_matches"]
    assert "rtl_blocks" not in state
    assert "always_comb result_o = rm_i;" not in state["final_context"]


def test_a_weak_spec_lane_is_not_requeried_through_the_bridge():
    records, signals, bridge = _runtime_data()
    queries = []

    def spec(query, k):
        queries.append(query)
        return [_doc("spec#1", "noise", section_id="sec:fpu", start_page=12)]

    app = agent.build_graph(
        spec_retriever=spec,
        rtl_retriever=lambda query, k: [_doc("rtl:fpu", "module fpu", module="fpu",
                                             file="rtl/fpu.sv", line=1)],
        answer_model=lambda prompt: "answer", spec_records=records, spec_signals=signals,
        bridge=bridge)

    agent.run(app, "What does fpu do?")

    assert len(queries) == 1 and not hasattr(agent, "reverse_bridge_terms")


def test_spec_signal_rows_stay_out_of_the_context():
    state = {"question": "q", "query_type": agent.SPEC,
             "spec_evidence": [{"id": "s1", "text": "AWVALID", "metadata": {"start_page": 1}}],
             "spec_signals": [{"name": "AWVALID", "width": "1", "table_id": "t"}]}

    assert "STRUCTURED SPEC SIGNALS" not in agent.merge_evidence(state)


def test_signals_and_section_alignments_match_evidence_by_hierarchy_id():
    evidence = [{"id": "c1", "metadata": {"hierarchy_id": "axi::sec::0042"}}]
    signal = {"signal_id": "sig:awvalid", "name": "AWVALID", "hierarchy_id": "axi::sec::0042"}
    bridge = {"signal_alignments": [], "unresolved_signals": [], "module_alignments": [
        {"spec_section_id": "axi::sec::0042", "spec_section_title": "Valid-Ready",
         "rtl_module_name": "cache", "confidence_level": "MEDIUM"}]}

    assert agent.lookup_bridge([], evidence, bridge)[1] == bridge["module_alignments"]


def test_rtl_records_come_from_the_rtl_chunks_artifact(monkeypatch, tmp_path):
    import inspect

    import retrieve

    path = tmp_path / "rtl_chunks.jsonl"
    path.write_text('{"chunk_id": "rtl:fpu", "text": "module fpu"}\n')
    monkeypatch.setattr(cfg, "RTL_CHUNKS_PATH", path)
    monkeypatch.setattr("embed.index_manifest_status", lambda *args: {"fresh": True})
    seen = []
    monkeypatch.setattr(retrieve.RTLRetriever, "search",
                        lambda self, query, top_k: seen.append(self.records) or [])

    assert "rtl" not in inspect.signature(agent.build_graph).parameters
    agent.Runtime().rtl_search("q", 1)
    assert seen == [[{"chunk_id": "rtl:fpu", "text": "module fpu"}]]


def _write_design_names(tmp_path, monkeypatch, rtl_names, spec_names):
    """Point the router at a tiny design and specification signal table."""
    rtl = {"modules": {"core": {"original_name": "core",
                                "ports": [{"original_name": name} for name in rtl_names],
                                "signals": [], "parameters": [], "instances": []}}}
    spec = {"signals": [{"name": name} for name in spec_names]}
    (tmp_path / "rtl.json").write_text(json.dumps(rtl), encoding="utf-8")
    (tmp_path / "spec_signals.json").write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(cfg, "RTL_PATH", tmp_path / "rtl.json")
    monkeypatch.setattr(cfg, "SPEC_SIGNALS_PATH", tmp_path / "spec_signals.json")
    agent._design_names.cache_clear()


def test_a_bus_signal_with_an_underscore_routes_to_the_specification(tmp_path, monkeypatch):
    _write_design_names(tmp_path, monkeypatch, ["StallD"], ["CYC_O"])

    assert agent.classify_query("What does CYC_O indicate on the Wishbone bus?") == agent.SPEC
    agent._design_names.cache_clear()


def test_a_name_only_the_design_declares_routes_to_the_rtl(tmp_path, monkeypatch):
    _write_design_names(tmp_path, monkeypatch, ["StallD", "rm_i"], ["CYC_O"])

    assert agent.classify_query("What sets StallD?") == agent.RTL
    assert agent.classify_query("WHAT SETS STALLD?") == agent.RTL
    agent._design_names.cache_clear()


def test_router_names_no_particular_standard():
    assert not re.search(r"axi|risc|timer", agent._SPEC_SOURCE.pattern + agent._LOCAL_SOURCE.pattern
                         + agent._IMPLEMENTATION_SUBJECT.pattern, re.IGNORECASE)


def test_asking_whether_anything_behaves_as_specified_is_a_comparison():
    assert agent.classify_query(
        "Does the link training behave as the PCIe specification requires?") == agent.COMPARISON
    assert agent.classify_query(
        "Does the specification allow READY to be asserted before VALID?") == agent.SPEC
