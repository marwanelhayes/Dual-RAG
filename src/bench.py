"""Evaluate Dual-RAG retrieval, alignment, routing, and comparison evidence use.

Flow: Load versioned labels -> run retrieval or answers -> score diagnostic metrics -> write a provenance-bound report.
"""

import json
import sys
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


def _mean(values, default=1.0):
    """Average a sequence, scoring an empty one as perfect rather than undefined.

    Args:
        values: Numeric or boolean scores to average.
        default: Value returned when nothing was scored.

    Returns:
        The arithmetic mean, or ``default`` when ``values`` is empty.
    """
    values = list(values)
    return sum(values) / len(values) if values else default


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


def mrr(cases, k=None):
    """Average the reciprocal rank of each case's first relevant result.

    Args:
        cases: ``(ranked_ids, relevant_ids)`` pairs.
        k: Optional rank cutoff; ``None`` scores the whole ranking.

    Returns:
        Mean reciprocal rank; empty cases score one.
    """
    def reciprocal(ranked, relevant):
        """Return one over the rank of the first relevant identifier, else zero.

        Args:
            ranked: Retrieved identifiers in rank order.
            relevant: Identifiers that count as a hit.

        Returns:
            The reciprocal rank, or ``0.0`` when nothing relevant was retrieved.
        """
        relevant = set(relevant)
        rank = next((index for index, item in enumerate(list(ranked)[:k], start=1)
                     if item in relevant), None)
        return 0.0 if rank is None else 1.0 / rank

    return _mean(reciprocal(ranked, relevant) for ranked, relevant in cases)


def top1_accuracy(cases):
    """Measure how often the first candidate is relevant.

    Args:
        cases: ``(ranked_ids, relevant_ids)`` pairs.

    Returns:
        Top-1 accuracy; empty cases score one.
    """
    return _mean(bool(ranked) and ranked[0] in set(relevant)
                 for ranked, relevant in cases)


def routing_accuracy(predicted, expected):
    """Measure exact three-way query classification accuracy.

    Args:
        predicted: Observed route labels.
        expected: Gold route labels.

    Returns:
        Accuracy over expected cases; missing predictions are wrong.
    """
    predicted = list(predicted)
    return _mean(index < len(predicted) and predicted[index] == value
                 for index, value in enumerate(expected))


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
    return {"recall_at_k": _mean(recall_at_k(ranked, relevant, k)
                                 for ranked, relevant in cases),
            "mrr": mrr(cases, k=k), "top1_accuracy": top1_accuracy(cases)}


def comparison_both_domain_rate(results):
    """Measure whether comparison routes retrieved both evidence domains.

    Args:
        results: Captured benchmark result rows.

    Returns:
        Fraction of comparison rows containing spec and RTL retrieval events.
    """
    return _mean(row.get("saw_spec") and row.get("saw_rtl") for row in results
                 if row.get("query_type") == "COMPARISON")


def citation_completeness(answer, citation):
    """Measure required evidence groups cited by an answer.

    Args:
        answer: Answer text.
        citation: Mapping containing alternative-string groups under ``required``.

    Returns:
        Fraction of citation groups represented.
    """
    folded = (answer or "").casefold()
    return _mean(any(option.casefold() in folded for option in group)
                 for group in citation.get("required", []))


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
    return {
        "spec_recall_at_k": _mean(recall_at_k(*case, k) for case in spec_cases),
        "spec_mrr": mrr(spec_cases, k=k),
        "rtl_recall_at_k": _mean(recall_at_k(*case, k) for case in rtl_cases),
        "rtl_mrr": mrr(rtl_cases, k=k),
        "routing_accuracy": routing_accuracy(
            [row.get("query_type") for row, _ in aligned],
            [gold.get("expected_query_type") for _, gold in aligned]),
        "alignment": alignment_metrics(predicted_alignments, expected_alignments, k=k),
        "comparison_both_domain_rate": comparison_both_domain_rate(results),
        "citation_completeness": _mean(
            citation_completeness(row.get("answer", ""), gold.get("citation", {}))
            for row, gold in aligned),
    }


def _skip_answer(context):
    """Stand in for the answer model so retrieval can be scored without generation.

    Args:
        context: Merged evidence package, ignored.

    Returns:
        Empty answer text.
    """
    del context
    return ""


def retrieved_ids(state):
    """Return the spec and RTL identifiers a question actually pulled into context.

    Args:
        state: Final state returned by ``agent.run``.

    Returns:
        ``(spec_ids, rtl_ids)`` in rank order.
    """
    spec_ids = [item.get("id") for item in state.get("spec_evidence", [])]
    rtl_ids = [item.get("id") for item in state.get("rtl_evidence", [])]
    return ([item for item in dict.fromkeys(spec_ids) if item],
            [item for item in dict.fromkeys(rtl_ids) if item])


def _chunk_family(chunk_id, lane):
    """Return the section or module family encoded in a stable chunk ID."""
    chunk_id = str(chunk_id)
    if lane == "spec":
        # ponytail: table IDs stay exact; use stored hierarchy metadata if that ceiling matters.
        parent, separator, suffix = chunk_id.rpartition("#")
        return parent if separator and suffix.isdigit() else chunk_id
    if lane == "rtl" and chunk_id.startswith("rtl:construct:"):
        return "rtl:" + chunk_id.split(":", 3)[2]
    return chunk_id


def section_key(chunk):
    """Return the outline anchor a spec chunk belongs to, stable across re-chunking.

    Args:
        chunk: Spec chunk record from ``chunks.jsonl``.

    Returns:
        ``doc::table::label`` for tables, else ``doc::section_number`` (or section name).
    """
    if chunk["chunk_type"] == "table":
        return f"{chunk['document_id']}::table::{chunk['table_label']}"
    return f"{chunk['document_id']}::{chunk['section_number'] or chunk['section_name']}"


def section_recall(ranked, labels, sections, k):
    """Score retrieval by the gold sections reached, not by exact chunk IDs.

    Args:
        ranked: Retrieved spec chunk IDs in rank order.
        labels: Gold ``{"chunk_id", "section", "page"}`` labels for one question.
        sections: Current chunk ID to ``section_key``.
        k: Rank cutoff.

    Returns:
        Fraction of labels whose section appears in the top k, or None without labels.
    """
    if not labels:
        return None
    top = {sections.get(item) for item in ranked[:k]}
    return _mean(label["section"] in top for label in labels)


def lane_result(ranked, relevant, k, groups=None, lane=None):
    """Score one evidence lane against its expected chunks.

    Args:
        ranked: Retrieved identifiers in rank order.
        relevant: Expected chunk identifiers.
        k: Rank cutoff for recall.
        groups: Alternative chunk IDs grouped by fact.
        lane: Domain name used to derive section or module families.

    Returns:
        Exact, family, and fact-group coverage at one shared cutoff.
    """
    if not relevant:
        return None
    ranked, relevant = list(ranked), list(relevant)
    top = ranked[:k]
    hits = [item for item in relevant if item in top]
    misses = [item for item in relevant if item not in top]
    groups = list(groups or [relevant])
    top_families = {_chunk_family(item, lane) for item in top}
    exact_recall = len(hits) / len(relevant)
    family_recall = _mean(_chunk_family(item, lane) in top_families for item in relevant)
    fact_coverage = _mean(any(item in top for item in group) for group in groups)
    first = next((index for index, item in enumerate(top, start=1)
                  if item in set(relevant)), None)
    return {
        "k": k, "expected": relevant, "groups": groups, "retrieved": ranked,
        "searched": bool(ranked),
        "hits_of_expected": f"{len(hits)} of {len(relevant)}",
        "hits": hits, "misses": misses,
        "exact_id_recall_at_k": exact_recall, "recall_at_k": exact_recall,
        "family_recall_at_k": family_recall,
        "fact_coverage_at_k": fact_coverage,
        "hit_at_k": bool(hits), "full_hit_at_k": not misses,
        "recall": exact_recall,
        "first_hit_rank": first,
    }


def retrieval_row(question, state, k, sections=None):
    """Project one question's retrieval behavior into a scored row.

    Args:
        question: Gold case record.
        state: Final graph state for that question.
        k: Rank cutoff for recall.
        sections: Optional chunk ID to ``section_key`` map for section recall.

    Returns:
        Row with route outcome and per-lane retrieval results.
    """
    spec_ids, rtl_ids = retrieved_ids(state)
    predicted = state.get("query_type")
    row = {
        "id": question["id"], "question": question["question"],
        "naming": question.get("naming"),
        "expected_query_type": question.get("expected_query_type"),
        "query_type": predicted,
        "route_correct": predicted == question.get("expected_query_type"),
        "spec": lane_result(
            spec_ids, question.get("relevant_spec_ids", []), k,
            question.get("relevant_spec_groups"), "spec"),
        "rtl": lane_result(
            rtl_ids, question.get("relevant_rtl_ids", []), k,
            question.get("relevant_rtl_groups"), "rtl"),
    }
    if row["spec"] and sections is not None:
        row["spec"]["section_recall_at_k"] = section_recall(
            spec_ids, question.get("relevant_spec_sections", []), sections, k)
    row["context"] = context_recall(question, state, sections or {})
    return row


def context_recall(question, state, sections):
    """Score the gold the answer model can cite, i.e. what survived context assembly.

    Args:
        question: Gold case record.
        state: Final graph state carrying ``citable_evidence`` and ``final_context``.
        sections: Chunk ID to ``section_key`` map.

    Returns:
        Spec section recall and RTL exact recall over citable passages (None without gold),
        and the context size in characters.
    """
    cited = [item for item in state.get("citable_evidence", {}).values()]
    spec = [item["id"] for item in cited if item["domain"] == "spec"]
    rtl = {item["id"] for item in cited if item["domain"] == "rtl"}
    relevant_rtl = question.get("relevant_rtl_ids", [])
    return {
        "spec_section_recall": section_recall(
            spec, question.get("relevant_spec_sections", []), sections, len(spec)),
        "rtl_exact_recall": (_mean(item in rtl for item in relevant_rtl)
                             if relevant_rtl else None),
        "chars": len(state.get("final_context", "")),
    }


def run_retrieval(questions, app=None, k=5):
    """Run every question through retrieval only and score the evidence lanes.

    Args:
        questions: Gold case records.
        app: Optional compiled graph; the default graph never calls the answer model.
        k: Rank cutoff for recall.

    Returns:
        Scored retrieval rows.
    """
    import agent

    app = agent.build_graph(answer_model=_skip_answer) if app is None else app
    with open(cfg.CHUNKS_PATH, encoding="utf-8") as handle:
        sections = {chunk["chunk_id"]: section_key(chunk)
                    for chunk in map(json.loads, handle)}
    rows = []
    for question in questions:
        started = time.perf_counter()
        state = agent.run(app, question["question"])
        row = retrieval_row(question, state, k, sections)
        row["seconds"] = round(time.perf_counter() - started, 3)
        rows.append(row)
    return rows


def stage_retrieval(questions, spec, rtl, k=5, sections=None):
    """Score each retriever on the raw question, without any agent layer.

    Args:
        questions: Gold case records.
        spec: Specification retriever exposing ``search(query, top_k)``.
        rtl: RTL retriever exposing ``search(query, top_k)``.
        k: Rank cutoff.
        sections: Optional chunk ID to ``section_key`` map for section recall.

    Returns:
        Scored rows; a lane is searched only for questions that expect it.
    """
    def evidence(hits):
        """Project retriever hits, bare or ``(document, score)``, to evidence IDs."""
        return [{"id": (hit[0] if isinstance(hit, tuple) else hit).metadata["chunk_id"]}
                for hit in hits]

    rows = []
    for question in questions:
        state = {}
        if question.get("relevant_spec_ids"):
            state["spec_evidence"] = evidence(spec.search(question["question"], top_k=k))
        if question.get("relevant_rtl_ids"):
            state["rtl_evidence"] = evidence(rtl.search(question["question"], top_k=k))
        rows.append(retrieval_row(question, state, k, sections))
    return rows


def _lane_summary(rows, lane):
    """Aggregate one lane across the rows that expect it.

    Args:
        rows: Scored retrieval rows.
        lane: ``"spec"`` or ``"rtl"``.

    Returns:
        Lane metric mapping; a lane no question expects reports only its zero count.
    """
    scored = [row[lane] for row in rows if row.get(lane)]
    if not scored:
        return {"questions": 0}
    cases = [(item["retrieved"], item["expected"]) for item in scored]
    cutoff = scored[0]["k"]
    stated = [item["hits_of_expected"] for item in scored]
    exact_recall = _mean(item["exact_id_recall_at_k"] for item in scored)
    reciprocal_rank = mrr(cases, k=cutoff)
    return {
        "questions": len(scored), "k": cutoff,
        "availability": _mean(item["searched"] for item in scored),
        "exact_id_recall_at_k": exact_recall, "recall_at_k": exact_recall,
        "family_recall_at_k": _mean(item["family_recall_at_k"] for item in scored),
        "fact_coverage_at_k": _mean(item["fact_coverage_at_k"] for item in scored),
        "section_recall_at_k": _mean(item["section_recall_at_k"] for item in scored
                                     if item.get("section_recall_at_k") is not None),
        "mean_recall": exact_recall,
        "mrr_at_k": reciprocal_rank, "mrr": reciprocal_rank,
        "hit_at_k": _mean(item["hit_at_k"] for item in scored),
        "any_hit_rate": _mean(item["hit_at_k"] for item in scored),
        "full_hit_at_k": _mean(item["full_hit_at_k"] for item in scored),
        "full_hit_rate": _mean(item["full_hit_at_k"] for item in scored),
        "hit_distribution": {label: stated.count(label) for label in sorted(set(stated))},
    }


def retrieval_metrics(rows):
    """Summarize routing and per-lane retrieval, split by route and signal naming.

    Args:
        rows: Scored retrieval rows.

    Returns:
        Metric mapping covering all rows plus route and naming slices.
    """
    def summarize(subset):
        """Return routing and lane metrics for one slice of rows.

        Args:
            subset: Scored retrieval rows belonging to this slice.

        Returns:
            Metric mapping, or only a zero question count for an empty slice.
        """
        if not subset:
            return {"questions": 0}
        return {
            "questions": len(subset),
            "routing_accuracy": _mean(row["route_correct"] for row in subset),
            "dual_evidence_recall_at_k": _mean((
                row["spec"]["hit_at_k"] and row["rtl"]["hit_at_k"]
                for row in subset if row.get("spec") and row.get("rtl")), default=0.0),
            "mean_seconds": _mean(row.get("seconds", 0) for row in subset),
            "spec": _lane_summary(subset, "spec"),
            "rtl": _lane_summary(subset, "rtl"),
            "context": {
                key: _mean((row["context"][key] for row in subset
                            if row.get("context", {}).get(key) is not None), default=0.0)
                for key in ("spec_section_recall", "rtl_exact_recall", "chars")},
        }

    routes = sorted({row["expected_query_type"] for row in rows if row["expected_query_type"]})
    namings = sorted({row["naming"] for row in rows if row["naming"]})
    return {
        "overall": summarize(rows),
        "by_route": {route: summarize([row for row in rows
                                       if row["expected_query_type"] == route])
                     for route in routes},
        "by_naming": {naming: summarize([row for row in rows
                                         if row["naming"] == naming])
                      for naming in namings},
    }


def retrieval_misses(rows):
    """List every expected chunk that retrieval failed to reach.

    Args:
        rows: Scored retrieval rows.

    Returns:
        One entry per question that missed at least one expected chunk.
    """
    misses = []
    for row in rows:
        entry = {lane: row[lane]["misses"] for lane in ("spec", "rtl")
                 if row.get(lane) and row[lane]["misses"]}
        if entry:
            misses.append({"id": row["id"], "question": row.get("question"), **entry})
    return misses


def hit_report(rows):
    """Render one line per question stating what each lane retrieved of what it owed.

    Args:
        rows: Scored retrieval rows.

    Returns:
        A fixed-width table naming the route outcome and per-lane hit counts.
    """
    def stated(row, lane):
        """Return one lane's ``hits of expected`` count, or a dash when unexpected.

        Args:
            row: One scored retrieval row.
            lane: ``"spec"`` or ``"rtl"``.

        Returns:
            The stated hit count, or ``"-"`` when the question expects this lane nothing.
        """
        return row[lane]["hits_of_expected"] if row.get(lane) else "-"

    width = max((len(str(row["id"])) for row in rows), default=8)
    lines = [f"{'question':<{width}}  route  spec      rtl",
             f"{'-' * width}  -----  --------  --------"]
    lines.extend(
        f"{row['id']:<{width}}  {'ok   ' if row['route_correct'] else 'WRONG'}  "
        f"{stated(row, 'spec'):<8}  {stated(row, 'rtl')}" for row in rows)
    return "\n".join(lines)


def stage_retrieval_report(questions, k=5):
    """Load both retrievers from the pipeline artifacts and score them stage-locally.

    Args:
        questions: Gold case records.
        k: Rank cutoff.

    Returns:
        ``{"metrics": {"spec", "rtl"}, "results": rows}``.
    """
    from embed import Embedder
    from ingest import load_chunks
    from retrieve import Retriever, RTLRetriever, shared_reranker

    spec_records = load_chunks()
    rtl_records = load_chunks(cfg.RTL_CHUNKS_PATH)
    embedder, reranker = Embedder(), shared_reranker()
    spec = Retriever.load(spec_records, embedder=embedder, reranker=reranker)
    rtl = RTLRetriever(rtl_records, hybrid=Retriever.load(
        rtl_records, None, reranker=reranker))
    sections = {chunk["chunk_id"]: section_key(chunk) for chunk in spec_records}
    rows = stage_retrieval(questions, spec, rtl, k, sections)
    return {"metrics": {lane: _lane_summary(rows, lane) for lane in ("spec", "rtl")},
            "results": rows}


def report_provenance(dataset):
    """Bind a benchmark to its exact labels, selected generation and model configuration."""
    from artifacts import file_hash

    return {"dataset_sha256": file_hash(dataset), "dataset": str(dataset),
            "generation": str(cfg.OUTPUT_DIR), "embedding_model": cfg.EMBED_MODEL,
            "embedding_revision": cfg.EMBED_REVISION, "reranker": cfg.RERANK_MODEL,
            "reranker_revision": cfg.RERANK_REVISION, "answer_model_digest": cfg.LOCAL_LLM_DIGEST}


def retrieval_main(path=None, k=5):
    """Score retrieval alone and write the per-question report.

    Args:
        path: Optional question-set path; defaults to the 60-question set.
        k: Rank cutoff for recall.

    Returns:
        None.
    """
    from agent import build_graph

    questions = load_gold(path or getattr(cfg, "QUESTION_SET_PATH", cfg.GOLD_PATH))
    rows = run_retrieval(questions, build_graph(answer_model=_skip_answer), k)
    report = {"k": k, "provenance": report_provenance(path or cfg.QUESTION_SET_PATH),
              "metrics": retrieval_metrics(rows),
              "misses": retrieval_misses(rows), "results": rows}
    report["retrieve"] = stage_retrieval_report(questions, k)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(cfg.RETRIEVAL_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(hit_report(rows))
    print()
    print(json.dumps(report["metrics"], indent=2))
    print(f"{len(report['misses'])} of {len(rows)} questions missed an expected chunk; "
          f"full report in {cfg.RETRIEVAL_PATH}")


def main():
    """Run the gold benchmark and write answers plus structured metrics.

    Returns:
        None.
    """
    from agent import build_graph

    questions = load_gold()
    results = run(build_graph(), questions)
    report = {"provenance": report_provenance(cfg.GOLD_PATH),
              "summary": tally(row["outcome"] for row in results),
              "metrics": aggregate_metrics(results, questions), "results": results}
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(cfg.BENCH_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report["summary"], indent=2))
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    if "--retrieval" in sys.argv[1:]:
        arguments = [item for item in sys.argv[1:] if item != "--retrieval"]
        retrieval_main(arguments[0] if arguments else None)
    else:
        main()
