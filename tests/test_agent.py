"""Hermetic contracts for the three-route Dual-RAG LangGraph."""

from langchain_core.documents import Document

import agent


def _doc(chunk_id, text, **metadata):
    return Document(page_content=text, metadata={"chunk_id": chunk_id, **metadata})


def _runtime_data():
    records = [{
        "chunk_id": "spec#1", "section_id": "sec:fpu", "subsection_id": "sec:round",
        "context_header": "Spec > FPU > Rounding", "text": "Rounding mode selects RNE.",
        "start_page": 12,
    }]
    hierarchy = {"sections": [
        {"section_id": "sec:fpu", "parent_id": None, "children_ids": ["sec:round"]},
        {"section_id": "sec:round", "parent_id": "sec:fpu", "children_ids": [],
         "prev_id": None, "next_id": None},
    ]}
    signals = [{
        "signal_id": "specsig:rounding_mode", "name": "rounding_mode",
        "normalized_name": "rounding mode", "width": 3, "direction": "input",
        "section_id": "sec:fpu", "subsection_id": "sec:round", "chunk_id": "spec#1",
        "table_id": "tbl:1",
    }]
    rtl = {"modules": {"fpu": {
        "original_name": "fpu", "always_blocks": [{
            "construct_id": "rtl:block:fpu:20", "file": "rtl/fpu.sv",
            "start_line": 20, "end_line": 32, "signals_read": ["rm_i"],
            "signals_written": ["result_o"], "source_text": "always_comb result_o = rm_i;",
        }], "functions_tasks": [], "assignments": [], "structural_chunks": [],
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
    return records, hierarchy, signals, rtl, bridge


def test_classifier_exposes_exactly_three_routes():
    assert agent.QUERY_TYPES == {agent.SPEC, agent.RTL, agent.COMPARISON}
    assert agent.classify_query("What rounding modes are required?") == agent.SPEC
    assert agent.classify_query("Where is rm_i driven in the RTL?") == agent.RTL
    assert agent.classify_query("Does the RTL implement every rounding mode?") == \
        agent.COMPARISON


def test_spec_route_never_runs_rtl_retrieval_or_bridge_lookup():
    calls = []
    records, hierarchy, signals, rtl, bridge = _runtime_data()

    def spec_search(query, k):
        calls.append("spec")
        return [_doc("spec#1", "Rounding is required.", section_id="sec:fpu",
                     subsection_id="sec:round", start_page=12)]

    def rtl_search(query, k):
        calls.append("rtl")
        return []

    app = agent.build_graph(
        spec_search, rtl_search, lambda prompt: "spec answer", records, hierarchy,
        signals, rtl, bridge)
    state = agent.run(app, "What rounding modes are required?")

    assert calls == ["spec"]
    assert state["query_type"] == agent.SPEC and state["answer"] == "spec answer"
    assert [event["event"] for event in state["trace"]] == [
        "route", "spec_retrieval", "evidence_merge", "answer",
    ]


def test_rtl_route_never_runs_spec_retrieval():
    calls = []
    records, hierarchy, signals, rtl, bridge = _runtime_data()

    def spec_search(query, k):
        calls.append("spec")
        return []

    def rtl_search(query, k):
        calls.append("rtl")
        return [_doc("rtl:fpu", "module fpu", module="fpu", file="rtl/fpu.sv", line=1)]

    app = agent.build_graph(
        spec_search, rtl_search, lambda prompt: "rtl answer", records, hierarchy,
        signals, rtl, bridge)
    state = agent.run(app, "Where is rm_i driven in the RTL?")

    assert calls == ["rtl"]
    assert state["query_type"] == agent.RTL and state["answer"] == "rtl answer"
    assert [event["event"] for event in state["trace"]] == [
        "route", "rtl_retrieval", "evidence_merge", "answer",
    ]


def test_comparison_structurally_runs_spec_then_bridge_then_targeted_rtl_then_model():
    calls = []
    records, hierarchy, signals, rtl, bridge = _runtime_data()

    def spec_search(query, k):
        calls.append("spec")
        return [_doc("spec#1", "Rounding modes are required.", section_id="sec:fpu",
                     subsection_id="sec:round", start_page=12)]

    def rtl_search(query, k):
        calls.append(("rtl", query))
        return [_doc("rtl:fpu", "module fpu", module="fpu", file="rtl/fpu.sv", line=1)]

    def answer_model(prompt):
        calls.append(("model", prompt))
        return "implemented with cited evidence"

    app = agent.build_graph(
        spec_search, rtl_search, answer_model, records, hierarchy, signals, rtl, bridge)
    state = agent.run(app, "Does the RTL implement every rounding mode?")

    assert calls[0] == "spec"
    # Bridge targets steer the block lane only; injecting them into the query buried
    # the question's own module under exact hits for whatever the spec lane drifted to.
    assert calls[1] == ("rtl", "Does the RTL implement every rounding mode?")
    assert calls[2][0] == "model"
    assert [event["event"] for event in state["trace"]] == [
        "route", "spec_retrieval", "bridge_lookup", "rtl_retrieval",
        "evidence_merge", "answer",
    ]
    assert state["rtl_blocks"][0]["start_line"] == 20


def test_comparison_refuses_to_call_model_when_either_domain_is_empty():
    model_calls = []
    records, hierarchy, signals, rtl, bridge = _runtime_data()
    app = agent.build_graph(
        lambda query, k: [_doc("spec#1", "spec", section_id="sec:fpu")],
        lambda query, k: [],
        lambda prompt: model_calls.append(prompt) or "unsafe",
        records, hierarchy, signals, rtl={"modules": {}}, bridge=bridge)

    state = agent.run(app, "Does the RTL implement every rounding mode?")

    assert model_calls == []
    assert state["answer"].startswith("INSUFFICIENT EVIDENCE")
    assert state["trace"][-1] == {"event": "answer", "model_called": False}


def test_section_expansion_uses_only_inline_hierarchy_links():
    evidence = [{
        "domain": "spec", "id": "child#1", "text": "child", "metadata": {
            "section_id": "child",
        },
    }]
    hierarchy = {"sections": [{
        "section_id": "child", "parent_id": "parent", "prev_id": "previous",
        "next_id": None, "children_ids": [],
    }]}
    records = [
        {"chunk_id": "parent#1", "section_id": "parent", "text": "parent",
         "context_header": "Parent"},
        {"chunk_id": "previous#1", "section_id": "previous", "text": "previous",
         "context_header": "Previous"},
    ]

    expanded = agent.expand_spec_hierarchy(evidence, hierarchy, records, limit=2)

    assert [item["id"] for item in expanded] == ["child#1", "parent#1", "previous#1"]


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
    _, _, _, rtl, _ = _runtime_data()

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


def test_sliding_window_near_duplicates_collapse_to_one_passage():
    """Drop chunks that merely re-open the previous chunk's sentences."""
    shared = ("The handshake has the following sequence of events: the master puts "
              "the address on AWADDR and asserts AWVALID in clock cycle two. ")
    records = [
        {"domain": "spec", "id": "a", "text": shared + "It completes on cycle four."},
        {"domain": "spec", "id": "b", "text": "Preamble sentence. " + shared},
        {"domain": "spec", "id": "c", "text": "Credited transport uses a wholly "
                                              "different crediting mechanism for AW."},
    ]

    kept = agent.drop_near_duplicates(records, size=8, threshold=0.25)

    assert [item["id"] for item in kept] == ["a", "c"]


def test_near_duplicate_filter_keeps_distinct_chunks_from_one_section():
    """Preserve neighbouring chunks that share only ordinary vocabulary."""
    records = [
        {"domain": "spec", "id": "a", "text": "For transactions on the write channels "
                                              "the manager must not wait for AWREADY."},
        {"domain": "spec", "id": "b", "text": "For transactions on the read channels "
                                              "the subordinate drives RVALID and RLAST."},
    ]

    kept = agent.drop_near_duplicates(records, size=8, threshold=0.25)

    assert [item["id"] for item in kept] == ["a", "b"]


def test_alignment_rows_label_the_score_as_identity_not_as_a_verdict():
    """Name the number after what it measures so it cannot read as a behavioural verdict."""
    matches = [{"spec_original_name": "AWVALID", "rtl_original_name": "MAWValid",
                "confidence_level": "WEAK", "confidence": 0.695039}]

    rendered = agent._bridge_context(matches, [])

    assert "identity_match=WEAK" in rendered
    assert "identity_score=0.695039" in rendered
    assert "confidence=" not in rendered
