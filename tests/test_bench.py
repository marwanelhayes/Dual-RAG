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
