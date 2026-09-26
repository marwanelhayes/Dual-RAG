"""Hermetic contracts for the three-route Dual-RAG LangGraph."""

from langchain_core.documents import Document

import agent
import configure as cfg


def _doc(chunk_id, text, **metadata):
    return Document(page_content=text, metadata={"chunk_id": chunk_id, **metadata})


_FLOORS = {"spec": -6.0, "rtl": -6.0}


class _Reranker:
    """Score lane candidates deterministically instead of loading a cross-encoder."""

    def __init__(self, scores=None, default=0.0):
        self.scores = scores or {}
        self.default = default

    def score_pairs(self, pairs):
        return [next((score for marker, score in self.scores.items() if marker in text),
                     self.default)
                for _, text in pairs]


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
    rtl = {"modules": {"fpu": {
        "module_id": "rtl::mod::fpu", "original_name": "fpu", "normalized_name": "fpu",
        "normalized_tokens": ["fpu"], "file": "rtl/fpu.sv", "start_line": 1,
        "end_line": 40, "source_code": "module fpu; endmodule", "preamble": "", "ports": [],
        "signals": [], "parameters": [], "instances": [], "always_blocks": [{
            "construct_id": "rtl:block:fpu:20", "block_id": "rtl::blk::fpu::always::20-32",
            "kind": "always", "construct_type": "ALWAYS_COMB", "original_name": "always@20",
            "file": "rtl/fpu.sv", "start_line": 20, "end_line": 32,
            "signals_read": ["rm_i"], "signals_written": ["result_o"],
            "source_code": "always_comb result_o = rm_i;",
            "source_text": "always_comb result_o = rm_i;",
        }], "functions_tasks": [], "assignment_groups": [], "assignments": [],
        "structural_chunks": [],
    }}}
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
    return records, signals, rtl, bridge


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
    records, signals, rtl, bridge = _runtime_data()

    def spec_search(query, k):
        calls.append("spec")
        return [_doc("spec#1", "Rounding is required.", section_id="sec:fpu",
                     subsection_id="sec:round", start_page=12)]

    def rtl_search(query, k):
        calls.append("rtl")
        return [_doc("rtl:fpu", "module fpu", module="fpu", file="rtl/fpu.sv", line=1)]

    app = agent.build_graph(
        spec_search, rtl_search, lambda prompt: "answer", records,
        signals, rtl, bridge, reranker=_Reranker(), lane_floors=_FLOORS)
    state = agent.run(app, "What rounding modes are required?")

    assert calls == ["spec", "rtl"]
    assert [event["event"] for event in state["trace"]] == [
        "route_prior", "spec_retrieval", "rtl_retrieval", "bridge_lookup",
        "evidence_expansion", "lane_allocation", "evidence_merge", "answer",
    ]


def test_requested_route_is_separate_from_observed_evidence():
    records, signals, rtl, bridge = _runtime_data()
    reranker = _Reranker({"module fpu": 2.0}, default=-9.0)
    app = agent.build_graph(
        lambda query, k: [_doc("spec#1", "unrelated prose", section_id="sec:fpu")],
        lambda query, k: [_doc("rtl:fpu", "module fpu", module="fpu",
                               file="rtl/fpu.sv", line=1)],
        lambda prompt: "answer", records, signals, rtl, bridge,
        reranker=reranker, lane_floors=_FLOORS)

    state = agent.run(app, "What causes a stall?")

    assert state["route_prior"] == agent.SPEC
    assert state["query_type"] == agent.SPEC
    assert state["answer_status"] == "insufficient_evidence"
    assert state["lane_credible"] == {"spec": False, "rtl": True}


def test_comparison_label_needs_both_lanes_above_the_floor():
    records, signals, rtl, bridge = _runtime_data()
    app = agent.build_graph(
        lambda query, k: [_doc("spec#1", "Rounding modes are required.",
                               section_id="sec:fpu", subsection_id="sec:round",
                               start_page=12)],
        lambda query, k: [_doc("rtl:fpu", "module fpu", module="fpu",
                               file="rtl/fpu.sv", line=1)],
        lambda prompt: "implemented with cited evidence",
        records, signals, rtl, bridge, reranker=_Reranker(), lane_floors=_FLOORS)

    state = agent.run(app, "Does the RTL implement every rounding mode?")

    assert state["query_type"] == agent.COMPARISON
    assert state["rtl_blocks"][0]["start_line"] == 20
    assert state["answer_status"] == "rejected"
    assert state["answer"].startswith("INSUFFICIENT EVIDENCE")


def test_weak_spec_lane_requeries_the_spec_through_reverse_bridge_terms():
    queries = []
    records, signals, rtl, bridge = _runtime_data()

    def spec_search(query, k):
        queries.append(query)
        if len(queries) == 1:
            return [_doc("spec#1", "unrelated prose", section_id="sec:fpu")]
        return [_doc("spec#2", "Rounding mode selects RNE.", section_id="sec:fpu",
                     subsection_id="sec:round", start_page=12)]

    app = agent.build_graph(
        spec_search,
        lambda query, k: [_doc("rtl:fpu", "module fpu", module="fpu",
                               file="rtl/fpu.sv", line=1)],
        lambda prompt: "answer", records, signals, rtl, bridge,
        reranker=_Reranker({"module fpu": 2.0, "Rounding mode": 1.0}, default=-9.0),
        lane_floors=_FLOORS)
    state = agent.run(app, "where does the pipeline stall")

    assert len(queries) == 2 and "rounding_mode" in queries[1]
    assert [item["id"] for item in state["spec_evidence"]] == ["spec#1", "spec#2"]


def test_model_is_refused_only_when_no_lane_cleared_the_floor():
    model_calls = []
    records, signals, rtl, bridge = _runtime_data()
    app = agent.build_graph(
        lambda query, k: [], lambda query, k: [],
        lambda prompt: model_calls.append(prompt) or "unsafe",
        records, signals, rtl={"modules": {}}, bridge=bridge,
        reranker=_Reranker(), lane_floors=_FLOORS)

    state = agent.run(app, "Does the RTL implement every rounding mode?")

    assert model_calls == []
    assert state["answer"].startswith("INSUFFICIENT EVIDENCE")
    assert state["trace"][-1] == {"event": "answer", "model_called": False}


def test_weak_lane_keeps_only_a_slice_of_the_context_budget():
    state = {
        "question": "q", "query_type": agent.SPEC,
        "spec_evidence": [{"id": "s1", "text": "spec " * 2000, "metadata": {}}],
        "rtl_evidence": [{"id": "r1", "text": "rtl " * 2000, "metadata": {}}],
        "lane_credible": {"spec": True, "rtl": False},
    }

    context = agent.merge_evidence(state)
    balanced = agent.merge_evidence({**state,
                                     "lane_credible": {"spec": True, "rtl": True}})

    assert context.count("rtl ") < balanced.count("rtl ")
    assert context.count("spec ") == balanced.count("spec ")
    assert len(context) < len(balanced)


def test_lane_floors_are_measured_against_each_domains_own_background():
    prose = [{"text": "The manager must not wait for AWREADY before asserting AWVALID."},
             {"text": "Register x0 is hardwired to zero in the base integer ISA."},
             {"text": "The mtvec MODE field selects direct or vectored trap entry."}]

    class _Scale:
        """Return a fixed background so the quantile, not the model, is under test."""

        def __init__(self, values):
            self.values = values

        def score_pairs(self, pairs):
            return self.values[:len(list(pairs))]

    probes = agent.background_queries(prose, limit=3)

    assert len(probes) == 3 and probes[0].startswith("The manager must not wait")
    assert agent.background_floor(_Scale([-9.0, -8.0, -2.0]), prose, probes,
                                  samples=3, quantile=0.95) == -2.0
    assert agent.background_floor(_Scale([-9.0, -8.0, -2.0]), prose, probes,
                                  samples=3, quantile=0.0) == -9.0
    assert agent.background_floor(None, prose, probes) == cfg.LANE_FLOOR_FALLBACK
    assert agent.background_floor(_Scale([]), [], probes) == cfg.LANE_FLOOR_FALLBACK


def test_credible_lanes_apply_the_floor_of_their_own_domain():
    scores = {"spec": -4.0, "rtl": -4.0}

    assert agent.credible_lanes(scores, {"spec": 0.0, "rtl": -6.0}) == {
        "spec": False, "rtl": True}
    assert agent.credible_lanes({"spec": None, "rtl": 1.0}, {"spec": -6.0, "rtl": -6.0}) == {
        "spec": False, "rtl": True}


def test_signal_selection_is_grounded_in_retrieved_provenance():
    signals = [
        {"signal_id": "a", "name": "rounding_mode", "chunk_id": "spec#1"},
        {"signal_id": "b", "name": "unrelated", "chunk_id": "spec#2"},
    ]
    evidence = [{"id": "spec#1", "metadata": {"section_id": "rounding"}}]

    selected = agent.select_spec_signals("rounding behavior", evidence, signals)

    assert [item["signal_id"] for item in selected] == ["a"]


def test_signal_selection_rejects_description_overlap_and_broad_parent_sections():
    signals = [{
        "signal_id": "axi", "name": "ARMMUVALID",
        "description": "floating-point implementation metadata",
        "section_id": "broad", "subsection_id": "unrelated", "chunk_id": "axi#1",
    }]
    evidence = [{"id": "riscv#1", "metadata": {
        "section_id": "broad", "subsection_id": "fpu", "table_ids": [],
    }}]

    selected = agent.select_spec_signals(
        "What is missing from the floating-point implementation?", evidence, signals)

    assert selected == []


def test_unresolved_bridge_record_exposes_only_qualified_candidates_as_search_targets():
    signals = [{"signal_id": "unknown", "name": "new_signal"}]
    artifact = {"signal_alignments": [], "unresolved_signals": [{
        "spec_signal_id": "unknown", "spec_original_name": "new_signal",
        "confidence_level": "UNRESOLVED", "candidates": [{
            "rtl_signal_id": "rtl::candidate", "rtl_original_name": "candidate_i",
            "rtl_module_name": "candidate_module", "score": 0.7,
            "survived_structural": True,
        }, {
            "rtl_signal_id": "rtl::weak", "rtl_original_name": "unrelated_i",
            "rtl_module_name": "unrelated_module", "score": 0.1,
            "survived_structural": False,
        }],
    }], "module_alignments": []}

    matches, modules = agent.lookup_bridge(signals, [], artifact)
    targets, target_modules = agent.bridge_targets(matches, modules)

    assert matches[0]["confidence_level"] == "UNRESOLVED"
    assert targets == ["candidate_i"] and target_modules == ["candidate_module"]


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


def _staged_rtl():
    """Return a module using the usual registered/next staging convention."""
    return {"modules": {"cache": {
        "original_name": "cache",
        "ports": [{"original_name": "MAWValid"}, {"original_name": "rst"},
                  {"original_name": "rst_n"}],
        "signals": [{"original_name": "MAWValid_next"}],
        "always_blocks": [
            {"construct_id": "rtl:block:cache:415", "file": "rtl/cache.sv",
             "start_line": 415, "end_line": 459, "signals_read": ["MAWValid_next"],
             "signals_written": ["MAWValid"],
             "source_text": "always_ff MAWValid <= MAWValid_next;"},
            {"construct_id": "rtl:block:cache:660", "file": "rtl/cache.sv",
             "start_line": 660, "end_line": 991, "signals_read": ["MAWReady"],
             "signals_written": ["MAWValid_next"],
             "source_text": "always_comb MAWValid_next = 1'b1;"},
            {"construct_id": "rtl:block:cache:900", "file": "rtl/cache.sv",
             "start_line": 900, "end_line": 910, "signals_read": [],
             "signals_written": ["rst_n"], "source_text": "always_comb rst_n = ~rst;"},
        ],
        "functions_tasks": [], "assignments": [], "structural_chunks": [],
    }}}


def test_blocks_follow_registered_and_next_staging_suffixes_both_ways():
    forward = agent.surrounding_rtl_blocks(_staged_rtl(), ["MAWValid"], [], limit=6)
    reverse = agent.surrounding_rtl_blocks(_staged_rtl(), ["MAWValid_next"], [], limit=6)

    assert [block["start_line"] for block in forward] == [415, 660]
    assert {block["start_line"] for block in reverse} == {415, 660}


def test_a_module_target_alone_expands_nothing_rather_than_the_first_blocks_in_the_file():
    blocks = agent.surrounding_rtl_blocks(_staged_rtl(), [], ["cache"], limit=6)

    assert blocks == []


def test_staging_hop_ignores_suffixes_that_are_not_a_staging_convention():
    blocks = agent.surrounding_rtl_blocks(_staged_rtl(), ["rst"], [], limit=6)

    assert [block["start_line"] for block in blocks] == []


def test_selected_blocks_record_which_targets_they_matched():
    blocks = agent.surrounding_rtl_blocks(_staged_rtl(), ["MAWValid"], [], limit=6)
    matched = {block["start_line"]: block["matched_signals"] for block in blocks}

    assert matched[415] == ["mawvalid", "mawvalid_next"]
    assert matched[660] == ["mawvalid_next"]


def test_oversized_blocks_are_excerpted_around_their_matched_signals(monkeypatch):
    monkeypatch.setattr(agent.cfg, "AGENT_RTL_BLOCK_CHARS", 120, raising=False)
    filler = "\n".join(f"    unrelated_{index} = 0;" for index in range(40))
    block = {
        "id": "b", "module": "cache", "file": "rtl/cache.sv",
        "start_line": 660, "end_line": 991, "matched_signals": ["mawvalid_next"],
        "text": filler + "\n    MAWValid_next = 1'b1;\n" + filler,
    }

    rendered = agent._rtl_context([], [block])

    assert "MAWValid_next = 1'b1;" in rendered
    assert "[...]" in rendered
    assert "unrelated_20 = 0;" not in rendered
    assert "unrelated_39 = 0;" in rendered
    assert len(rendered) < len(block["text"])


def test_targeted_blocks_outrank_fuzzy_rtl_evidence_in_the_lane():
    evidence = [{"id": "e", "text": "fuzzy vector hit",
                 "metadata": {"file": "rtl/other.sv", "start_line": 9}}]
    block = {
        "id": "b", "module": "cache", "file": "rtl/cache.sv",
        "start_line": 660, "end_line": 991, "matched_signals": ["mawvalid_next"],
        "text": "MAWValid_next = 1'b1;",
    }

    rendered = agent._rtl_context(evidence, [block])

    assert rendered.index("cache.sv:660") < rendered.index("other.sv:9")


def test_small_blocks_are_never_excerpted():
    block = {
        "id": "b", "module": "cache", "file": "rtl/cache.sv",
        "start_line": 1, "end_line": 2, "matched_signals": ["mawvalid"],
        "text": "always_ff MAWValid <= MAWValid_next;",
    }

    rendered = agent._rtl_context([], [block])

    assert "always_ff MAWValid <= MAWValid_next;" in rendered
    assert "[...]" not in rendered


def test_surrounding_blocks_require_a_target_module_or_signal():
    _, _, rtl, _ = _runtime_data()

    hit = agent.surrounding_rtl_blocks(rtl, ["rm_i"], [], limit=2)
    miss = agent.surrounding_rtl_blocks(rtl, ["other"], [], limit=2)

    assert hit[0]["id"] == "rtl:block:fpu:20"
    assert miss == []


def test_merged_context_is_bounded_and_separates_domains(monkeypatch):
    monkeypatch.setattr(agent.cfg, "AGENT_CONTEXT_CHARS", 500, raising=False)
    state = {
        "question": "Does this match?", "query_type": agent.COMPARISON,
        "spec_evidence": [{"id": "s", "text": "s" * 500,
                           "metadata": {"start_page": 1}}],
        "spec_signals": [], "bridge_matches": [], "module_matches": [],
        "rtl_evidence": [{"id": "r", "text": "r" * 500,
                          "metadata": {"file": "r.sv", "line": 2}}],
        "rtl_blocks": [],
    }

    context = agent.merge_evidence(state)

    assert len(context) <= 500
    assert "SPEC EVIDENCE" in context and "RTL EVIDENCE" in context
    assert context.endswith("[TRUNCATED]")


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


def test_targeted_blocks_are_relabelled_with_store_chunk_ids():
    blocks = [{"id": "cache:415", "module": "cache", "start_line": 415, "end_line": 459},
              {"id": "cache:9", "module": "cache", "start_line": 9, "end_line": 11}]
    records = [{"chunk_id": "rtl:construct:cache:always:415-459", "source_type": "construct",
                "module": "cache", "start_line": 415, "end_line": 459}]

    aligned = agent.align_block_ids(blocks, records)

    assert aligned[0]["id"] == "rtl:construct:cache:always:415-459"
    assert aligned[1]["id"] == "cache:9"


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
        spec_records=records, spec_signals=[], rtl={"modules": {}}, bridge={},
        reranker=_Reranker(), lane_floors=_FLOORS)

    state = agent.run(app, "What rounding modes are required?")

    assert [item["id"] for item in state["spec_evidence"]] == ["doc#0010#001"]
