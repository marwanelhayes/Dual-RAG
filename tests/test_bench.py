"""Hermetic metrics for Dual-RAG retrieval, alignment, and route enforcement."""

import pytest

import agent
import bench


def test_answer_scorer_handles_facts_forbidden_claims_and_abstention(monkeypatch):
    gold = {"requires": [["FMADD"], ["FMSUB"]], "forbids": ["fully implements"],
            "abstention_is_correct": False}

    assert bench.score("FMADD and FMSUB are absent.", gold) == bench.CORRECT
    assert bench.score("The design fully implements floating point.", gold) == bench.WRONG
    assert bench.score("Only FMADD is absent.", gold) == bench.UNVERIFIED
    assert bench.score("INSUFFICIENT EVIDENCE", gold) == bench.ABSTAINED


def test_tally_includes_zero_count_outcomes():
    assert bench.tally([bench.CORRECT, bench.WRONG]) == {
        bench.CORRECT: 1, bench.WRONG: 1, bench.UNVERIFIED: 0,
        bench.ABSTAINED: 0,
    }


def test_tally_counts_every_outcome_from_a_single_pass_iterator():
    outcomes = (label for label in (bench.CORRECT, bench.UNVERIFIED, bench.UNVERIFIED))

    assert bench.tally(outcomes) == {
        bench.CORRECT: 1, bench.WRONG: 0, bench.UNVERIFIED: 2,
        bench.ABSTAINED: 0,
    }


def test_recall_at_k_counts_distinct_relevant_hits():
    ranked = ["noise", "b", "b", "a"]
    relevant = {"a", "b"}

    assert bench.recall_at_k(ranked, relevant, 2) == 0.5
    assert bench.recall_at_k(ranked, relevant, 4) == 1.0
    assert bench.recall_at_k(ranked, set(), 2) == 1.0


def test_mrr_and_top1_use_the_first_relevant_candidate():
    cases = [(["noise", "wanted"], {"wanted"}), (["hit", "noise"], {"hit"})]

    assert bench.mrr(cases) == pytest.approx(0.75)
    assert bench.top1_accuracy(cases) == 0.5
    assert bench.mrr([(["noise"], {"missing"})]) == 0.0


def test_routing_accuracy_penalizes_missing_predictions():
    assert bench.routing_accuracy(["SPEC", "RTL"], ["SPEC", "RTL", "COMPARISON"]) \
        == pytest.approx(2 / 3)
    assert bench.routing_accuracy([], []) == 1.0


def test_alignment_metrics_report_recall_mrr_and_top1():
    predicted = {"round": ["wrong", "rm_i"], "result": ["result_o"]}
    expected = {"round": ["rm_i"], "result": ["result_o"]}

    metrics = bench.alignment_metrics(predicted, expected, k=2)

    assert metrics == {"recall_at_k": 1.0, "mrr": 0.75, "top1_accuracy": 0.5}


def test_comparison_both_domain_rate_checks_structured_retrieval_events():
    rows = [
        {"query_type": "COMPARISON", "saw_spec": True, "saw_rtl": True},
        {"query_type": "COMPARISON", "saw_spec": True, "saw_rtl": False},
        {"query_type": "SPEC", "saw_spec": True, "saw_rtl": False},
    ]

    assert bench.comparison_both_domain_rate(rows) == 0.5


def test_capture_state_keeps_ranked_ids_alignment_and_context_size():
    state = {
        "query_type": "COMPARISON",
        "trace": [{"event": "spec_retrieval"}, {"event": "rtl_retrieval"}],
        "spec_evidence": [{"id": "spec#1"}], "rtl_evidence": [{"id": "rtl:fpu"}],
        "bridge_matches": [{
            "spec_signal_id": "round", "rtl_signal": "rm_i",
            "candidates": [{"rtl_signal": "rm_alt"}],
        }],
        "final_context": "bounded",
    }

    captured = bench.capture_state(state)

    assert captured["saw_spec"] and captured["saw_rtl"]
    assert captured["ranked_spec_ids"] == ["spec#1"]
    assert captured["ranked_rtl_ids"] == ["rtl:fpu"]
    assert captured["alignment_candidates"] == {"round": ["rm_i", "rm_alt"]}
    assert captured["final_context_chars"] == 7


def test_aggregate_metrics_cover_both_rags_router_bridge_and_comparison_path():
    results = [{
        "id": "C1", "query_type": "COMPARISON", "saw_spec": True, "saw_rtl": True,
        "ranked_spec_ids": ["spec#1"], "ranked_rtl_ids": ["rtl:fpu"],
        "alignment_candidates": {"round": ["rm_i"]},
        "answer": "[spec:spec#1 p.1] and fpu.sv:2",
    }]
    gold = [{
        "id": "C1", "expected_query_type": "COMPARISON",
        "relevant_spec_ids": ["spec#1"], "relevant_rtl_ids": ["rtl:fpu"],
        "expected_alignments": {"round": ["rm_i"]},
        "citation": {"required": [["spec#1"], ["fpu.sv:2"]]},
    }]

    metrics = bench.aggregate_metrics(results, gold, k=1)

    assert metrics["spec_recall_at_k"] == metrics["rtl_recall_at_k"] == 1.0
    assert metrics["routing_accuracy"] == 1.0
    assert metrics["alignment"]["top1_accuracy"] == 1.0
    assert metrics["comparison_both_domain_rate"] == 1.0
    assert metrics["citation_completeness"] == 1.0


def test_run_captures_fresh_state_for_each_question(monkeypatch):
    states = iter([
        {"query_type": "SPEC", "trace": [{"event": "spec_retrieval"}],
         "spec_evidence": [], "answer": "first"},
        {"query_type": "RTL", "trace": [{"event": "rtl_retrieval"}],
         "rtl_evidence": [], "answer": "second"},
    ])
    monkeypatch.setattr(agent, "run", lambda app, question: next(states))
    questions = [
        {"id": "S", "question": "spec", "requires": [["first"]], "forbids": []},
        {"id": "R", "question": "rtl", "requires": [["second"]], "forbids": []},
    ]

    results = bench.run(object(), questions)

    assert [row["query_type"] for row in results] == ["SPEC", "RTL"]
    assert all(row["outcome"] == bench.CORRECT for row in results)


def test_repository_gold_uses_current_non_vacuous_three_route_schema():
    records = bench.load_gold()
    required = {
        "expected_query_type", "relevant_spec_ids", "relevant_rtl_ids",
        "expected_alignments",
    }

    assert records and all(required <= set(record) for record in records)
    assert {record["expected_query_type"] for record in records} == {
        "SPEC", "RTL", "COMPARISON",
    }
    assert all(agent.classify_query(record["question"]) == record["expected_query_type"]
               for record in records)
    assert any(record["expected_alignments"] for record in records)
    assert any(record["relevant_spec_ids"] for record in records)
    assert any(record["relevant_rtl_ids"] for record in records)


def _retrieval_graph(answer_calls):
    """Build a graph whose lanes return fixed documents and whose model records calls."""
    from langchain_core.documents import Document

    def spec_search(query, k):
        del query, k
        return [Document(page_content="AWVALID is asserted high.",
                         metadata={"chunk_id": "axi#1"})]

    def rtl_search(query, k):
        del query, k
        return [Document(page_content="always_comb StallD = LWStall;",
                         metadata={"chunk_id": "rtl:hazard_unit"})]

    def answer_model(context):
        answer_calls.append(context)
        return "should not be scored"

    class _Reranker:
        """Score both lanes deterministically instead of loading a cross-encoder."""

        def score_pairs(self, pairs):
            return [0.0 for _ in pairs]

    return agent.build_graph(
        spec_retriever=spec_search, rtl_retriever=rtl_search, answer_model=answer_model,
        reranker=_Reranker(), lane_floors={"spec": -6.0, "rtl": -6.0},
        spec_records=[], spec_signals=[],
        rtl={"modules": {}}, bridge={"signal_alignments": [], "unresolved_signals": [],
                                     "module_alignments": []})


def test_lane_result_uses_one_cutoff_and_scores_alternatives_and_families():
    ranked = ["noise", "doc#0010#000", "doc#0010#001"]
    relevant = ["doc#0010#000", "doc#0010#001", "other"]
    result = bench.lane_result(
        ranked, relevant, k=2,
        groups=[["doc#0010#000", "doc#0010#001"], ["other"]], lane="spec")

    assert result["hits"] == ["doc#0010#000"]
    assert result["misses"] == ["doc#0010#001", "other"]
    assert result["exact_id_recall_at_k"] == 1 / 3
    assert result["family_recall_at_k"] == 2 / 3
    assert result["fact_coverage_at_k"] == 0.5
    assert result["hit_at_k"] and not result["full_hit_at_k"]
    assert result["first_hit_rank"] == 2
    assert bench.lane_result(["a"], [], k=5) is None


def test_retrieved_ids_keep_lane_order_without_duplicates():
    state = {"spec_evidence": [{"id": "s1"}, {"id": "s1"}],
             "rtl_evidence": [{"id": "r1"}, {"id": "r2"}, {"id": "r1"}]}

    assert bench.retrieved_ids(state) == (["s1"], ["r1", "r2"])


def test_retrieval_scoring_reads_lanes_only_and_discards_the_answer():
    answer_calls = []
    questions = [{"id": "RTL_ONE", "question": "Where is StallD driven in the RTL?",
                  "naming": "exact", "expected_query_type": "RTL",
                  "relevant_spec_ids": [],
                  "relevant_rtl_ids": ["rtl:hazard_unit", "rtl:missing"]}]

    rows = bench.run_retrieval(questions, _retrieval_graph(answer_calls))

    assert "answer" not in rows[0] and "outcome" not in rows[0]
    assert rows[0]["spec"] is None and rows[0]["rtl"]["searched"]
    assert rows[0]["rtl"]["hits"] == ["rtl:hazard_unit"]
    assert rows[0]["rtl"]["misses"] == ["rtl:missing"]


def test_default_retrieval_graph_never_generates_an_answer(monkeypatch):
    import agent

    captured = {}
    stub = _retrieval_graph([])

    def build_graph(**kwargs):
        captured.update(kwargs)
        return stub

    monkeypatch.setattr(agent, "build_graph", build_graph)
    bench.run_retrieval([{"id": "X", "question": "Where is StallD driven in the RTL?",
                          "expected_query_type": "RTL", "relevant_spec_ids": [],
                          "relevant_rtl_ids": ["rtl:hazard_unit"]}])

    assert captured["answer_model"] is bench._skip_answer
    assert bench._skip_answer("any bounded context") == ""


def test_retrieval_metrics_slice_by_route_and_naming():
    rows = [
        {"id": "A", "naming": "exact", "expected_query_type": "SPEC", "query_type": "SPEC",
         "route_correct": True, "spec": bench.lane_result(["s1"], ["s1"], 5), "rtl": None},
        {"id": "B", "naming": "plain", "expected_query_type": "RTL", "query_type": "SPEC",
         "route_correct": False, "spec": None, "rtl": bench.lane_result([], ["r1"], 5)},
    ]

    metrics = bench.retrieval_metrics(rows)

    assert metrics["overall"]["routing_accuracy"] == 0.5
    assert metrics["by_route"]["SPEC"]["spec"]["full_hit_rate"] == 1.0
    assert metrics["by_naming"]["plain"]["rtl"]["any_hit_rate"] == 0.0
    assert metrics["by_naming"]["plain"]["rtl"]["availability"] == 0.0
    assert bench.retrieval_misses(rows) == [{"id": "B", "question": None, "rtl": ["r1"]}]


def test_section_key_uses_outline_number_name_or_table_label():
    prose = {"document_id": "axi", "chunk_type": "prose", "section_number": "A2.3.1",
             "section_name": "Valid-Ready signals"}
    unnumbered = {"document_id": "isa", "chunk_type": "prose", "section_number": "",
                  "section_name": "Preface"}
    table = {"document_id": "axi", "chunk_type": "table", "section_number": "A2.3.1",
             "section_name": "Valid-Ready signals", "table_label": "A2.2"}

    assert bench.section_key(prose) == "axi::A2.3.1"
    assert bench.section_key(unnumbered) == "isa::Preface"
    assert bench.section_key(table) == "axi::table::A2.2"


def test_section_recall_survives_chunk_renumbering():
    """Gold labels match by section, so re-split chunk IDs still count."""
    labels = [{"chunk_id": "axi#0042#000", "section": "axi::A2.3.1", "page": 30},
              {"chunk_id": "axi#table#A2.2#0000", "section": "axi::table::A2.2", "page": 30}]
    sections = {"axi#0051#003": "axi::A2.3.1", "axi#0099#000": "axi::B1"}

    assert bench.section_recall(["axi#0099#000", "axi#0051#003"], labels, sections, k=2) == 0.5
    assert bench.section_recall(["axi#0051#003"], [], sections, k=2) is None


def test_retrieval_row_and_summary_carry_section_recall():
    question = {"id": "q", "question": "?", "relevant_spec_ids": ["axi#0042#000"],
                "relevant_spec_sections": [
                    {"chunk_id": "axi#0042#000", "section": "axi::A2.3.1", "page": 30}]}
    state = {"query_type": "SPEC", "spec_evidence": [{"id": "axi#0051#003"}]}

    row = bench.retrieval_row(question, state, 5, sections={"axi#0051#003": "axi::A2.3.1"})

    assert row["spec"]["section_recall_at_k"] == 1.0
    assert bench._lane_summary([row], "spec")["section_recall_at_k"] == 1.0


def test_stage_retrieval_scores_each_retriever_on_the_raw_question():
    """Stage metrics bypass the agent: the question goes straight to each retriever."""
    from langchain_core.documents import Document

    def doc(chunk_id):
        return Document(page_content="", metadata={"chunk_id": chunk_id})

    class Spec:
        def search(self, query, top_k):
            assert query == "What is AWVALID?"
            return [(doc("axi#0051#003"), 1.0)]

    class RTL:
        def search(self, query, top_k):
            return [doc("rtl:cache")]

    questions = [
        {"id": "s", "question": "What is AWVALID?", "relevant_spec_ids": ["axi#0042#000"],
         "relevant_spec_sections": [
             {"chunk_id": "axi#0042#000", "section": "axi::A2.3.1", "page": 30}]},
        {"id": "r", "question": "Where is the cache?", "relevant_rtl_ids": ["rtl:cache"]},
    ]

    rows = bench.stage_retrieval(questions, Spec(), RTL(), 5,
                                 sections={"axi#0051#003": "axi::A2.3.1"})

    assert bench._lane_summary(rows, "spec")["section_recall_at_k"] == 1.0
    assert bench._lane_summary(rows, "rtl")["exact_id_recall_at_k"] == 1.0


def test_context_recall_counts_gold_the_model_can_cite():
    """Context metrics score what reached the model, not what was retrieved."""
    question = {"id": "q", "question": "?", "relevant_spec_ids": ["axi#0042#000"],
                "relevant_spec_sections": [
                    {"chunk_id": "axi#0042#000", "section": "axi::A2.3.1", "page": 30}],
                "relevant_rtl_ids": ["rtl:cache", "rtl:fetch_stage"]}
    state = {"query_type": "COMPARISON", "final_context": "x" * 40,
             "spec_evidence": [{"id": "axi#0051#003"}],
             "rtl_evidence": [{"id": "rtl:cache"}, {"id": "rtl:fetch_stage"}],
             "citable_evidence": {
                 "[spec:axi#0051#003 p.30]": {"domain": "spec", "id": "axi#0051#003"},
                 "[rtl:cache.sv:1]": {"domain": "rtl", "id": "rtl:cache"}}}

    row = bench.retrieval_row(question, state, 5, sections={"axi#0051#003": "axi::A2.3.1"})
    context = bench.retrieval_metrics([row])["overall"]["context"]

    assert context == {"spec_section_recall": 1.0, "rtl_exact_recall": 0.5, "chars": 40}
