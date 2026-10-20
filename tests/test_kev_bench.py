"""A wrong route must lose evidence in the experiment's reported scores."""

from langchain_core.documents import Document

import agent
import kev_bench


def test_paired_comparison_counts_skipped_lanes_and_penalizes_wrong_route():
    spec = Document(page_content="Required behavior", metadata={
        "chunk_id": "spec#1", "start_page": 1, "section_id": "section"})
    rtl = Document(page_content="assign a = b;", metadata={
        "chunk_id": "rtl:m", "file": "rtl/m.sv", "line": 1})
    calls = []
    def spec_search(q, k):
        calls.append("spec")
        return [spec]
    def rtl_search(q, k):
        calls.append("rtl")
        return [rtl]
    baseline = agent.Runtime(spec_search, rtl_search, lambda context: "",
                             [], [], {}, retrieval_mode="all")
    candidate = agent.Runtime(spec_search, rtl_search, lambda context: "",
                              [], [], {}, retrieval_mode="kev",
                              router=lambda q: {"query_type": "SPEC", "selected": "SPEC",
                                                "source": "kev", "fallback": None})
    questions = [{"id": "comparison", "question": "Compare the RTL against the spec",
                  "expected_query_type": "COMPARISON", "relevant_spec_ids": ["spec#1"],
                  "relevant_rtl_ids": ["rtl:m"]}]
    rows = kev_bench.run_comparison(questions, baseline, candidate, {})
    all_summary = kev_bench.summarize(rows["all"])
    kev_summary = kev_bench.summarize(rows["kev"])
    assert calls == ["spec", "rtl", "spec"]
    assert all_summary["lane_calls"] == 2
    assert kev_summary["lane_calls"] == 1
    assert all_summary["metrics"]["overall"]["rtl"]["exact_id_recall_at_k"] == 1
    assert kev_summary["metrics"]["overall"]["rtl"]["exact_id_recall_at_k"] == 0
    assert kev_summary["raw_routing_accuracy"] == 0
    assert kev_summary["metrics"]["overall"]["routing_accuracy"] == 0
