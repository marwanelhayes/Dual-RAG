"""Held-out answer evaluation with non-vacuous gates and hash-bound human reviews."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import configure as cfg


# Provisional internal-pilot targets, subject to owner approval and representative labels.
THRESHOLDS = {"required_fact_coverage": 0.90, "correct_abstention_rate": 0.95,
              "max_unsupported_claims": 0, "max_invalid_citations": 0}


def digest(value):
    """Hash the exact serialized dataset or answer a reviewer approved."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_dataset(dataset, development):
    """Reject leaked questions and vacuous facts; human review remains mandatory."""
    if dataset.get("split") not in {"heldout_candidate", "heldout"}:
        raise ValueError("explicit held-out split required")
    cases = dataset.get("cases", [])
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("nonempty unique cases required")
    normalize = lambda text: " ".join(text.casefold().split())
    existing = {normalize(case["question"]) for case in development}
    questions = [normalize(case["question"]) for case in cases]
    if len(set(questions)) != len(questions) or existing.intersection(questions):
        raise ValueError("duplicate or development question in held-out set")
    for case in cases:
        if case.get("intent") not in {"SPEC", "RTL", "COMPARISON"}:
            raise ValueError("invalid intent")
        if type(case.get("answerable")) is not bool:
            raise ValueError("answerability must be explicit")
        facts = case.get("facts", [])
        if case["answerable"] and not facts:
            raise ValueError("answerable cases need required facts")
        if len({fact["id"] for fact in facts}) != len(facts):
            raise ValueError("duplicate fact IDs")
        if any(not fact.get("description") or not fact.get("source_ids") for fact in facts):
            raise ValueError("facts require descriptions and alternative source IDs")


def run_cases(dataset, app=None):
    """Generate real answers once; never substitute a retrieval-only answer stub."""
    import agent

    app = agent.build_graph() if app is None else app
    rows = []
    for case in dataset["cases"]:
        started = time.perf_counter()
        try:
            state = agent.run(app, case["question"])
            answer = state.get("answer", "")
            validation = state.get("answer_validation", {})
            rows.append({"id": case["id"], "answer": answer, "answer_sha256": digest(answer),
                         "status": state.get("answer_status"),
                         "query_type": state.get("query_type"),
                         "validation": validation, "citable_evidence": state.get("citable_evidence", {}),
                         "trace": state.get("trace", []),
                         "seconds": time.perf_counter() - started})
        except Exception as exc:
            rows.append({"id": case["id"], "status": "error", "error": type(exc).__name__,
                         "answer": "", "answer_sha256": digest(""),
                         "seconds": time.perf_counter() - started})
    return rows


def release_gate(dataset, rows, reviews=None):
    """Require reviewed labels, matching reviewed answers, and all safety/quality gates."""
    reviews = reviews or {}
    blockers = []
    if dataset.get("split") != "heldout" or not dataset.get("reviewed_by"):
        blockers.append("heldout_labels_not_reviewed")
    if not dataset.get("thresholds_approved_by") or dataset.get("approved_thresholds") != THRESHOLDS:
        blockers.append("thresholds_not_approved")
    if reviews.get("dataset_sha256") != digest(dataset) or not reviews.get("reviewer"):
        blockers.append("answer_review_missing_or_stale")
    cases = {case["id"]: case for case in dataset["cases"]}
    if {case["intent"] for case in cases.values()} != {"SPEC", "RTL", "COMPARISON"}:
        blockers.append("missing_intent_coverage")
    by_id = {row["id"]: row for row in rows}
    if set(by_id) != set(cases) or len(rows) != len(cases):
        blockers.append("incomplete_or_duplicate_results")
    reviewed = {row["id"]: row for row in reviews.get("cases", [])}
    facts_total = facts_found = unanswered_total = abstentions_correct = unsupported = 0
    facts_reviewed = unanswered_reviewed = 0
    invalid_citations = 0
    for key, case in cases.items():
        row, review = by_id.get(key, {}), reviewed.get(key, {})
        errors = row.get("validation", {}).get("errors", [])
        invalid_citations += int("invalid_citation" in errors)
        if row.get("status") in {None, "error", "rejected"}:
            blockers.append(f"failed_answer:{key}")
        if row.get("answer_sha256") != digest(row.get("answer", "")):
            blockers.append(f"answer_hash_mismatch:{key}")
        if row.get("status") == "evidence_found" and not row.get("validation", {}).get("valid"):
            blockers.append(f"unvalidated_answer:{key}")
        if case["intent"] == "COMPARISON" and row.get("query_type") != "COMPARISON":
            blockers.append(f"comparison_intent_lost:{key}")
        fact_ids = {fact["id"] for fact in case.get("facts", [])}
        facts_total += len(fact_ids) if case["answerable"] else 0
        unanswered_total += int(not case["answerable"])
        present = review.get("facts_present")
        valid_review = (review.get("answer_sha256") == row.get("answer_sha256")
                        and isinstance(present, list) and all(isinstance(x, str) for x in present)
                        and len(present) == len(set(present)) and set(present) <= fact_ids
                        and type(review.get("unsupported_claims")) is int
                        and review["unsupported_claims"] >= 0
                        and (case["answerable"] or type(review.get("abstention_correct")) is bool))
        if not valid_review:
            blockers.append(f"unreviewed_answer:{key}")
            continue
        unsupported += review["unsupported_claims"]
        if case["answerable"]:
            facts_reviewed += len(fact_ids)
            evidence = row.get("citable_evidence", {})
            cited_ids = {evidence[claim["citation"]]["id"]
                         for claim in row.get("validation", {}).get("claims", [])
                         if claim.get("citation") in evidence}
            supported = [fact for fact in case["facts"] if fact["id"] in present
                         and cited_ids.intersection(fact["source_ids"])]
            facts_found += len(supported) if row.get("status") == "evidence_found" else 0
        else:
            unanswered_reviewed += 1
            abstentions_correct += int(row.get("status") == "insufficient_evidence"
                                       and review["abstention_correct"])
    coverage = facts_found / facts_total if facts_total and facts_reviewed == facts_total else None
    abstention = (abstentions_correct / unanswered_total
                  if unanswered_total and unanswered_reviewed == unanswered_total else None)
    if coverage is None or coverage < THRESHOLDS["required_fact_coverage"]:
        blockers.append("required_fact_coverage")
    if abstention is None or abstention < THRESHOLDS["correct_abstention_rate"]:
        blockers.append("correct_abstention_rate")
    if unsupported > THRESHOLDS["max_unsupported_claims"]:
        blockers.append("unsupported_claims")
    if invalid_citations > THRESHOLDS["max_invalid_citations"]:
        blockers.append("invalid_citations")
    return {"release_pass": not blockers, "blockers": blockers, "thresholds": THRESHOLDS,
            "required_fact_coverage": coverage, "correct_abstention_rate": abstention,
            "unsupported_claims": (unsupported if facts_reviewed == facts_total
                                   and unanswered_reviewed == unanswered_total else None),
            "invalid_citations": invalid_citations}


def main(argv=None):
    """Generate or rescore held-out answers and return a failing exit until approved."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=cfg.ROOT / "eval/heldout_release.json")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--results", type=Path, help="Review saved answers without regenerating them")
    args = parser.parse_args(argv)
    dataset = json.loads(args.dataset.read_text())
    development = json.loads((cfg.ROOT / "eval/question_set.json").read_text())
    development += json.loads(cfg.GOLD_PATH.read_text())
    development += json.loads((cfg.ROOT / "eval/release_development.json").read_text())
    validate_dataset(dataset, development)
    reviews = json.loads(args.reviews.read_text()) if args.reviews else None
    if args.results:
        saved = json.loads(args.results.read_text())
        if saved.get("dataset_sha256") != digest(dataset):
            raise SystemExit("saved results belong to a different dataset")
        rows = saved["results"]
    else:
        # Fail visibly before a long evaluation if the real model is unavailable.
        import httpx
        try:
            response = httpx.get(cfg.LOCAL_LLM_HOST.rstrip("/") + "/api/tags", timeout=5)
            response.raise_for_status()
            names = {item["name"] for item in response.json().get("models", [])}
            if cfg.LOCAL_LLM_MODEL not in names:
                raise ValueError("configured answer model is not installed")
        except Exception as exc:
            print(json.dumps({"release_pass": False, "blockers": ["model_unavailable"],
                              "error": type(exc).__name__}))
            return 1
        rows = run_cases(dataset)
    gate = release_gate(dataset, rows, reviews)
    report = {"dataset": dataset, "dataset_sha256": digest(dataset), "model": cfg.LOCAL_LLM_MODEL,
              "gate": gate, "results": rows,
              "review_template": {"dataset_sha256": digest(dataset), "reviewer": "",
                  "cases": [{"id": row["id"], "answer_sha256": row["answer_sha256"],
                             "facts_present": [], "unsupported_claims": None,
                             "abstention_correct": None} for row in rows]}}
    path = cfg.OUTPUT_DIR / "release_bench.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(gate, indent=2))
    print(f"Full report and review template: {path}")
    return 0 if gate["release_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
