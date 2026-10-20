"""Compare Kev-selected retrieval with the original two-lane graph, without answer generation.

Flow: Verify corpus and Kev -> warm shared retrievers -> alternate both graph modes per question -> save scores, traces and provenance.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import subprocess
import time

import agent
import artifacts
import bench
import configure as cfg
from kev_router import KevRouter


def run_comparison(questions, baseline, candidate, sections):
    """Alternate execution order; count skipped lanes and score against unchanged gold."""
    graphs = {"all": agent.build_graph(runtime=baseline),
              "kev": agent.build_graph(runtime=candidate)}
    rows = {mode: [] for mode in graphs}
    for index, question in enumerate(questions):
        order = ("all", "kev") if index % 2 == 0 else ("kev", "all")
        for mode in order:
            started = time.perf_counter()
            state = agent.run(graphs[mode], question["question"])
            row = bench.retrieval_row(question, state, 5, sections)
            row.update(seconds=time.perf_counter() - started,
                       routing=state.get("routing"), trace=state.get("trace", []))
            rows[mode].append(row)
        print(f"{index + 1}/{len(questions)} {question['id']}: "
              f"{rows['kev'][-1]['query_type']} "
              f"expected={question['expected_query_type']}", flush=True)
    return rows


def summarize(rows):
    """Report effective/raw routing, evidence coverage, fallbacks and real lane calls."""
    return {
        "metrics": bench.retrieval_metrics(rows),
        "median_seconds": statistics.median(row["seconds"] for row in rows),
        "lane_calls": sum(event["event"] in {"spec_retrieval", "rtl_retrieval"}
                          for row in rows for event in row["trace"]),
        "fallbacks": dict(Counter(row["routing"]["fallback"] for row in rows
                                  if row.get("routing") and row["routing"].get("fallback"))),
        "raw_routing_accuracy": (statistics.mean(
            row["routing"].get("selected") == row["expected_query_type"] for row in rows)
            if any(row.get("routing") for row in rows) else None),
        "misses": bench.retrieval_misses(rows), "results": rows,
    }


def main(argv=None):
    """Run the fixed development comparison; this never invokes the answer provider."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=cfg.QUESTION_SET_PATH)
    parser.add_argument("--output", type=Path, default=(
        cfg.OUTPUT_ROOT / "experiments" / "kev-routed-retrieval" / "report.json"))
    args = parser.parse_args(argv)
    questions = bench.load_gold(args.questions)
    if not questions:
        raise ValueError("benchmark requires at least one question")
    manifest = artifacts.verify_generation(cfg.OUTPUT_DIR)
    router = KevRouter()
    model_info = router.preflight()  # Do not label a disconnected fallback as a Kev experiment.
    baseline = agent.Runtime(answer_model=bench._skip_answer, retrieval_mode="all")
    candidate = agent.Runtime(
        baseline.spec_search, baseline.rtl_search, bench._skip_answer,
        baseline.get_spec_records(), baseline.get_spec_signals(), baseline.get_bridge(),
        retrieval_mode="kev", router=router)
    sections = {record["chunk_id"]: bench.section_key(record)
                for record in baseline.get_spec_records()}
    started = time.perf_counter()
    baseline.spec_search("startup evidence check", cfg.AGENT_SPEC_SEARCH_K)
    baseline.rtl_search("startup evidence check", cfg.AGENT_RTL_SEARCH_K)
    agent.answer_tokenizer()  # Tokenizer only; no answer-model weights or API requests.
    router("What does the AXI specification require?")
    warmup_seconds = time.perf_counter() - started
    rows = run_comparison(questions, baseline, candidate, sections)
    report = {
        "complete": True, "answer_generation": False,
        "provenance": {**bench.report_provenance(args.questions),
                       "source_hashes": artifacts.source_hashes(),
                       "corpus_source_hashes": manifest["inputs"],
                       "git_commit": subprocess.check_output(
                           ["git", "rev-parse", "HEAD"], cwd=cfg.ROOT, text=True).strip(),
                       "kev": model_info, "kev_run": cfg.KEV_RUN,
                       "minimum_probability": router.min_probability},
        "warmup_seconds": warmup_seconds,
        **{mode: summarize(items) for mode, items in rows.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for mode in ("all", "kev"):
        summary = report[mode]
        print(mode, json.dumps({key: summary[key] for key in (
            "metrics", "median_seconds", "lane_calls", "fallbacks", "raw_routing_accuracy")}))
    print(f"report: {args.output}")


if __name__ == "__main__":
    main()
