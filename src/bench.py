"""Evaluate Dual-RAG retrieval, alignment, routing, and comparison evidence use."""

import json
import time

import configure as cfg


CORRECT = "correct"
WRONG = "wrong"
UNVERIFIED = "unverified"
ABSTAINED = "abstained"


def load_gold(path=cfg.GOLD_PATH):
    """Read hand-checked benchmark cases.

    Args:
        path: Gold JSON path.

    Returns:
        Gold case dictionaries.
    """
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def score(answer, gold):
    """Score required facts, forbidden claims, and honest abstentions.

    Args:
        answer: Generated answer text.
        gold: Gold case with requires/forbids and abstention policy.

    Returns:
        One answer-level outcome label.
    """
    folded = (answer or "").casefold()
    insufficient = getattr(cfg, "AGENT_INSUFFICIENT", "INSUFFICIENT").casefold()
    if folded.startswith(insufficient) or folded.startswith("insufficient evidence"):
        return CORRECT if gold.get("abstention_is_correct") else ABSTAINED
    if any(term.casefold() in folded for term in gold.get("forbids", [])):
        return WRONG
    required = gold.get("requires", [])
    if all(any(option.casefold() in folded for option in group) for group in required):
        return CORRECT
    return UNVERIFIED


def tally(outcomes):
    """Count every answer outcome, including zero-count labels.

    Args:
        outcomes: Outcome labels.

    Returns:
        Count mapping for all supported labels.
    """
    counted = list(outcomes)
    return {label: counted.count(label)
            for label in (CORRECT, WRONG, UNVERIFIED, ABSTAINED)}


def recall_at_k(ranked, relevant, k):
    """Measure distinct relevant retrieval coverage within the first ``k`` IDs.

    Args:
        ranked: Retrieved identifiers in rank order.
        relevant: Relevant identifier collection.
        k: Rank cutoff.

    Returns:
        Recall in ``[0, 1]``; empty ground truth is fully recalled.
    """
    relevant = set(relevant)
    if not relevant:
        return 1.0
    return len(set(ranked[:k]).intersection(relevant)) / len(relevant)


def mean_reciprocal_rank(cases, k=None):
    """Return mean reciprocal rank of the first relevant result per case.

    Args:
        cases: ``(ranked_ids, relevant_ids)`` pairs.
        k: Optional rank cutoff.

    Returns:
        Mean reciprocal rank; empty cases score one.
    """
    cases = list(cases)
    if not cases:
        return 1.0
    scores = []
    for ranked, relevant in cases:
        relevant = set(relevant)
        ranked = list(ranked)[:k] if k is not None else list(ranked)
        rank = next((index for index, item in enumerate(ranked, start=1)
                     if item in relevant), None)
        scores.append(0.0 if rank is None else 1.0 / rank)
    return sum(scores) / len(scores)


def mrr(cases, k=None):
    """Alias ``mean_reciprocal_rank`` for compact reports.

    Args:
        cases: Ranked/relevant case pairs.
        k: Optional rank cutoff.

    Returns:
        Mean reciprocal rank.
    """
    return mean_reciprocal_rank(cases, k=k)


def top1_accuracy(cases):
    """Measure how often the first candidate is relevant.

    Args:
        cases: ``(ranked_ids, relevant_ids)`` pairs.

    Returns:
        Top-1 accuracy; empty cases score one.
    """
    cases = list(cases)
    if not cases:
        return 1.0
    return sum(bool(ranked) and ranked[0] in set(relevant)
               for ranked, relevant in cases) / len(cases)


def routing_accuracy(predicted, expected):
    """Measure exact three-way query classification accuracy.

    Args:
        predicted: Observed route labels.
        expected: Gold route labels.

    Returns:
        Accuracy over expected cases; missing predictions are wrong.
    """
    expected = list(expected)
    predicted = list(predicted)
    if not expected:
        return 1.0
    return sum(index < len(predicted) and predicted[index] == value
               for index, value in enumerate(expected)) / len(expected)


def alignment_metrics(predicted, expected, k=5):
    """Measure bridge candidate Recall@K, MRR, and Top-1 accuracy.

    Args:
        predicted: Spec signal ID to ranked RTL target IDs.
        expected: Spec signal ID to relevant RTL target IDs.
        k: Recall cutoff.

    Returns:
        Alignment metric mapping.
    """
    cases = [(predicted.get(key, []), targets) for key, targets in expected.items()]
    recall = (sum(recall_at_k(ranked, relevant, k) for ranked, relevant in cases)
              / len(cases) if cases else 1.0)
    return {"recall_at_k": recall, "mrr": mrr(cases, k=k),
            "top1_accuracy": top1_accuracy(cases)}


def comparison_both_domain_rate(results):
    """Measure whether comparison routes retrieved both evidence domains.

    Args:
        results: Captured benchmark result rows.

    Returns:
        Fraction of comparison rows containing spec and RTL retrieval events.
    """
    comparisons = [row for row in results if row.get("query_type") == "COMPARISON"]
    if not comparisons:
        return 1.0
    return sum(row.get("saw_spec") and row.get("saw_rtl")
               for row in comparisons) / len(comparisons)


def citation_completeness(answer, citation):
    """Measure required evidence groups cited by an answer.

    Args:
        answer: Answer text.
        citation: Mapping containing alternative-string groups under ``required``.

    Returns:
        Fraction of citation groups represented.
    """
    groups = citation.get("required", [])
    if not groups:
        return 1.0
    folded = (answer or "").casefold()
    return sum(any(option.casefold() in folded for option in group)
               for group in groups) / len(groups)


def _target_values(mapping):
    """Return ranked RTL targets from one bridge mapping.

    Args:
        mapping: Signal alignment record.

    Returns:
        Unique target IDs or names in candidate order.
    """
    values = []
    sources = [mapping, mapping.get("rtl", {}), *(mapping.get("candidates") or [])]
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = (source.get("rtl_signal_id") or source.get("rtl_signal")
                 or source.get("rtl_name") or source.get("signal_name"))
        if value and value not in values:
            values.append(value)
    return values


def capture_state(state):
    """Project final LangGraph state into stable benchmark fields.

    Args:
        state: Final state returned by ``agent.run``.

    Returns:
        JSON-compatible trace and evidence summary.
    """
    trace = state.get("trace", [])
    events = {item.get("event") for item in trace}
    alignments = {}
    for mapping in state.get("bridge_matches", []):
        key = (mapping.get("spec_signal_id") or mapping.get("spec_signal")
               or mapping.get("spec_name") or mapping.get("spec", {}).get("id"))
        if key:
            alignments[key] = _target_values(mapping)
    return {
        "query_type": state.get("query_type"),
        "trace": trace,
        "saw_spec": "spec_retrieval" in events,
        "saw_rtl": "rtl_retrieval" in events,
        "ranked_spec_ids": [item.get("id") for item in state.get("spec_evidence", [])],
        "ranked_rtl_ids": [item.get("id") for item in state.get("rtl_evidence", [])],
        "alignment_candidates": alignments,
        "final_context_chars": len(state.get("final_context", "")),
    }


def run(app, questions):
    """Run benchmark questions with independent graph state.

    Args:
        app: Compiled Dual-RAG graph.
        questions: Gold case records.

    Returns:
        Result rows with answers and structured traces.
    """
    import agent

    results = []
    for question in questions:
        started = time.perf_counter()
        state = agent.run(app, question["question"])
        answer = state.get("answer", "")
        results.append({
            "id": question["id"], "question": question["question"],
            "outcome": score(answer, question),
            "seconds": round(time.perf_counter() - started, 3),
            "answer": answer, **capture_state(state),
        })
    return results


def aggregate_metrics(results, gold_records, k=5):
    """Aggregate retrieval, routing, alignment, and evidence-path metrics.

    Args:
        results: Captured result rows.
        gold_records: Corresponding gold records.
        k: Retrieval and alignment cutoff.

    Returns:
        Metric mapping.
    """
    by_id = {row["id"]: row for row in results}
    aligned = [(by_id[item["id"]], item) for item in gold_records if item["id"] in by_id]
    spec_cases = [(row.get("ranked_spec_ids", []), gold.get("relevant_spec_ids", []))
                  for row, gold in aligned if gold.get("relevant_spec_ids")]
    rtl_cases = [(row.get("ranked_rtl_ids", []), gold.get("relevant_rtl_ids", []))
                 for row, gold in aligned if gold.get("relevant_rtl_ids")]
    predicted_alignments = {}
    expected_alignments = {}
    for row, gold in aligned:
        predicted_alignments.update(row.get("alignment_candidates", {}))
        expected_alignments.update(gold.get("expected_alignments", {}))
    spec_recall = (sum(recall_at_k(*case, k) for case in spec_cases) / len(spec_cases)
                   if spec_cases else 1.0)
    rtl_recall = (sum(recall_at_k(*case, k) for case in rtl_cases) / len(rtl_cases)
                  if rtl_cases else 1.0)
    citations = [citation_completeness(row.get("answer", ""), gold.get("citation", {}))
                 for row, gold in aligned]
    return {
        "spec_recall_at_k": spec_recall,
        "spec_mrr": mrr(spec_cases, k=k),
        "rtl_recall_at_k": rtl_recall,
        "rtl_mrr": mrr(rtl_cases, k=k),
        "routing_accuracy": routing_accuracy(
            [row.get("query_type") for row, _ in aligned],
            [gold.get("expected_query_type") for _, gold in aligned]),
        "alignment": alignment_metrics(predicted_alignments, expected_alignments, k=k),
        "comparison_both_domain_rate": comparison_both_domain_rate(results),
        "citation_completeness": sum(citations) / len(citations) if citations else 1.0,
    }


def main():
    """Run the gold benchmark and write answers plus structured metrics.

    Returns:
        None.
    """
    from agent import build_graph

    questions = load_gold()
    results = run(build_graph(), questions)
    report = {"summary": tally(row["outcome"] for row in results),
              "metrics": aggregate_metrics(results, questions), "results": results}
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(cfg.BENCH_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report["summary"], indent=2))
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
