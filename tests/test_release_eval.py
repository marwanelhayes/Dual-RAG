"""Release gates cannot pass with missing cases, stale reviews, or vacuous metrics."""

import json

import pytest

import configure as cfg
import release_eval as release


def reviewed_run():
    dataset = {"split": "heldout", "reviewed_by": "fixture-reviewer",
               "thresholds_approved_by": "fixture-owner", "approved_thresholds": dict(release.THRESHOLDS), "cases": [
        {"id": "a", "question": "fact?", "intent": "SPEC", "answerable": True,
         "facts": [{"id": "f", "description": "required fact", "source_ids": ["s", "alternative"]}]},
        {"id": "b", "question": "proof?", "intent": "COMPARISON", "answerable": False,
         "facts": []},
        {"id": "c", "question": "source?", "intent": "RTL", "answerable": True,
         "facts": [{"id": "g", "description": "source fact", "source_ids": ["r"]}]}]}
    rows = [{"id": "a", "answer": "quoted fact", "answer_sha256": release.digest("quoted fact"),
             "status": "evidence_found", "query_type": "SPEC",
             "validation": {"valid": True, "claims": [{"citation": "label"}]},
             "citable_evidence": {"label": {"id": "alternative"}}},
            {"id": "b", "answer": "insufficient", "answer_sha256": release.digest("insufficient"),
             "status": "insufficient_evidence", "query_type": "COMPARISON"},
            {"id": "c", "answer": "source fact", "answer_sha256": release.digest("source fact"),
             "status": "evidence_found", "query_type": "RTL",
             "validation": {"valid": True, "claims": [{"citation": "label"}]},
             "citable_evidence": {"label": {"id": "r"}}}]
    reviews = {"dataset_sha256": release.digest(dataset), "reviewer": "fixture-reviewer",
               "cases": [{"id": row["id"], "answer_sha256": row["answer_sha256"],
                          "facts_present": {"a": ["f"], "b": [], "c": ["g"]}[row["id"]],
                          "unsupported_claims": 0, "abstention_correct": True} for row in rows]}
    return dataset, rows, reviews


def test_gate_accepts_reviewed_alternative_sources_and_correct_abstention():
    gate = release.release_gate(*reviewed_run())
    assert gate["release_pass"]
    assert gate["required_fact_coverage"] == gate["correct_abstention_rate"] == 1


def test_unreviewed_answers_have_unknown_quality_and_changed_thresholds_need_approval():
    dataset, rows, reviews = reviewed_run()
    gate = release.release_gate(dataset, rows)
    assert gate["required_fact_coverage"] is None
    assert gate["correct_abstention_rate"] is None
    assert gate["unsupported_claims"] is None
    dataset["approved_thresholds"]["required_fact_coverage"] = 0.5
    assert "thresholds_not_approved" in release.release_gate(dataset, rows, reviews)["blockers"]


@pytest.mark.parametrize("mutation", ["no_review", "stale", "missing", "unsupported", "wrong_source",
                                      "downgrade", "rejected", "unapproved", "all_abstain"])
def test_gate_fails_closed(mutation):
    dataset, rows, reviews = reviewed_run()
    if mutation == "no_review":
        reviews = None
    elif mutation == "stale":
        reviews["cases"][0]["answer_sha256"] = "stale"
    elif mutation == "missing":
        rows.pop()
    elif mutation == "unsupported":
        reviews["cases"][0]["unsupported_claims"] = 1
    elif mutation == "wrong_source":
        rows[0]["citable_evidence"]["label"]["id"] = "unrelated"
    elif mutation == "downgrade":
        rows[1]["query_type"] = "SPEC"
    elif mutation == "rejected":
        rows[0]["status"] = "rejected"
    elif mutation == "unapproved":
        dataset["thresholds_approved_by"] = None
    else:
        rows[0]["status"] = "insufficient_evidence"
    assert not release.release_gate(dataset, rows, reviews)["release_pass"]


def test_dataset_rejects_leakage_and_requires_nonempty_facts():
    dataset, _, _ = reviewed_run()
    with pytest.raises(ValueError, match="development"):
        release.validate_dataset(dataset, [{"question": "FACT?"}])
    dataset["cases"][0]["facts"] = []
    with pytest.raises(ValueError, match="required facts"):
        release.validate_dataset(dataset, [])


def test_starter_heldout_is_separate_and_explicitly_unreviewed():
    dataset = json.loads((cfg.ROOT / "eval/heldout_release.json").read_text())
    development = json.loads(cfg.QUESTION_SET_PATH.read_text())
    development += json.loads(cfg.GOLD_PATH.read_text())
    development += json.loads((cfg.ROOT / "eval/release_development.json").read_text())
    release.validate_dataset(dataset, development)
    assert dataset["split"] == "heldout_candidate" and not dataset["reviewed_by"]
    assert not release.release_gate(dataset, [], None)["release_pass"]
    assert {case["category"] for case in dataset["cases"]} >= {
        "contradiction", "unanswerable", "renamed_identifiers", "unsupported_feature", "misleading_evidence"}


@pytest.mark.skipif(not cfg.CHUNKS_PATH.exists() or not cfg.RTL_CHUNKS_PATH.exists(),
                    reason="source artifacts are not built")
def test_starter_fact_sources_exist_in_the_current_snapshot():
    dataset = json.loads((cfg.ROOT / "eval/heldout_release.json").read_text())
    ids = {json.loads(line)["chunk_id"] for path in (cfg.CHUNKS_PATH, cfg.RTL_CHUNKS_PATH)
           for line in path.read_text().splitlines() if line.strip()}
    for case in dataset["cases"]:
        for fact in case["facts"]:
            assert set(fact["source_ids"]) <= ids


def test_release_runner_uses_real_answer_path_and_records_generation_failures(monkeypatch):
    import agent
    dataset, _, _ = reviewed_run()
    options = []
    monkeypatch.setattr(agent, "build_graph", lambda **kwargs: options.append(kwargs) or object())
    monkeypatch.setattr(agent, "run", lambda app, question: {
        "answer": "INSUFFICIENT EVIDENCE", "answer_status": "rejected",
        "answer_validation": {"errors": ["model_error:ConnectError"]}})
    rows = release.run_cases(dataset)
    assert options == [{"query_recovery": False}]
    assert all(row["status"] == "rejected" for row in rows)
