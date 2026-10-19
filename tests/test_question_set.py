"""Keep the 60-question benchmark set pointing at sections and chunks that still exist."""

import json

import pytest

import agent
import bench
import configure as cfg


QUESTION_SET = cfg.ROOT / "eval" / "question_set.json"


def _chunk_ids(path):
    with open(path, encoding="utf-8") as handle:
        return {json.loads(line)["chunk_id"] for line in handle if line.strip()}


@pytest.fixture(scope="module")
def questions():
    with open(QUESTION_SET, encoding="utf-8") as handle:
        return json.load(handle)


def test_question_set_has_twenty_cases_per_route(questions):
    counts = {}
    for case in questions:
        counts[case["expected_query_type"]] = counts.get(case["expected_query_type"], 0) + 1

    assert counts == {"SPEC": 20, "RTL": 20, "COMPARISON": 20}
    assert len({case["id"] for case in questions}) == len(questions)


def test_rtl_and_comparison_mix_exact_and_plain_signal_naming(questions):
    for route in ("RTL", "COMPARISON"):
        naming = [case["naming"] for case in questions
                  if case["expected_query_type"] == route]

        assert naming.count("exact") == 10
        assert naming.count("plain") == 10


def test_declared_routes_match_the_deterministic_classifier(questions, routing_vocabulary):
    mismatches = {
        case["id"]: agent.classify_query(case["question"])
        for case in questions
        if agent.classify_query(case["question"]) != case["expected_query_type"]
    }

    assert mismatches == {}


def test_every_case_names_alternative_chunks_for_each_scored_domain(questions):
    for case in questions:
        spec, rtl = case["relevant_spec_ids"], case["relevant_rtl_ids"]

        assert len(set(spec)) == len(spec) and len(set(rtl)) == len(rtl), case["id"]
        if case["expected_query_type"] == "SPEC":
            assert spec and not rtl, case["id"]
        elif case["expected_query_type"] == "RTL":
            assert rtl and not spec, case["id"]
        else:
            assert spec and rtl, case["id"]
        for lane, ids in (("spec", spec), ("rtl", rtl)):
            groups = case.get(f"relevant_{lane}_groups", [ids] if ids else [])
            assert all(group and set(group) <= set(ids) for group in groups), case["id"]


@pytest.mark.skipif(not (cfg.OUTPUT_DIR / "chunks.jsonl").exists(),
                    reason="ingested chunk stores are not built")
def test_expected_evidence_exists_in_the_ingested_stores(questions):
    """Spec gold is checked by section, which re-chunking does not renumber."""
    with open(cfg.OUTPUT_DIR / "chunks.jsonl", encoding="utf-8") as handle:
        spec_sections = {bench.section_key(json.loads(line)) for line in handle if line.strip()}
    rtl_ids = _chunk_ids(cfg.OUTPUT_DIR / "rtl_chunks.jsonl")

    for case in questions:
        assert {label["section"] for label in case["relevant_spec_sections"]} <= \
            spec_sections, case["id"]
        assert set(case["relevant_rtl_ids"]) <= rtl_ids, case["id"]
