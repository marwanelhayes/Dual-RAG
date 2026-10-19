# Answer safety and release evaluation

The runtime returns validated source excerpts, not free-form behavioral verdicts.
The native answer model selects up to three visible source labels through a bounded
JSON schema. The application copies their exact original text into canonical JSON
with `status` (`evidence_found` or `insufficient_evidence`) and `claims` containing
only `citation` and `quote`. Injected test/alternate adapters emit that canonical
form directly. Every citation must identify a
complete passage actually present in the prompt with a known page or source line.
Quotes must match that passage verbatim, allowing only whitespace normalization.
Unknown locations, truncated passages, altered operators/numbers, unsupported verdicts,
and malformed responses fail closed. The public answer contains only validated quotes
and fixed policy language. Source quote validation establishes provenance, not relevance,
complete coverage, or semantic entailment; these still require evaluation and review.

Request intent is retained in `query_type` regardless of what the other lane retrieves.
Both citable domains and citations
from both domains are required to return comparison excerpts. Such answers are labeled
`UNVERIFIED COMPARISON`. A name match or retrieval miss never proves implementation,
correctness, or absence. The old retrieval-text absence inference has been removed.
No simulation/formal-proof adapter is implemented: behavioral proof is outside this contract.

## Held-out workflow

`eval/question_set.json`, `eval/gold_questions.json`, and `eval/release_development.json` are development sets.
`eval/heldout_release.json` is a new **agent-authored candidate**, not an independently
validated held-out suite. Its facts permit alternative source IDs and do not require
three chunks per question. A domain reviewer must check every label and source, check
topic overlap with development cases, add representative design/revision coverage,
then set `split` to `heldout` and record `reviewed_by`. Do not tune on the held-out set;
if it informs an implementation change, move affected cases into development and replenish it.

The provisional internal-pilot gates are 90% required-fact coverage, 95% correct
abstention on unanswerable cases, zero unsupported claims, and zero invalid citations.
The owner must approve these targets, set `thresholds_approved_by`, and copy the report's
exact threshold mapping into `approved_thresholds`. Changed thresholds invalidate approval.
Unreviewed answer-quality metrics are reported as unknown, not zero or perfect. These are starting
targets, not evidence that the starter cases establish statistical release confidence.

The first live eight-case run exposed an intent-promotion issue. Its FENCE.I and
PC-adder cases were moved into development before fixing it. A fresh, unevaluated mux
case replenishes RTL/renamed-signal coverage; seven held-out candidates remain. A human
must still expand and approve representative coverage. The evaluated eight-case snapshot is saved locally in
`output/evaluated_release_dataset.json`; its results do not certify the subsequent fix.

With the configured local answer model running:

```bash
python src/release_eval.py
```

This runs actual generation, not the retrieval-only stub, and writes
`output/release_bench.json` with answers, exact visible evidence, validation failures,
timings, and a review template. Model unavailability fails the preflight with a nonzero
exit; it is never treated as a successful abstention evaluation.

A human reviews each answer for relevance, omitted conditions, factual coverage,
misleading excerpts, unsupported claims, and appropriate abstention. Fill in the
review template in a separate JSON file. `facts_present` contains required fact IDs;
credit also requires a citation to an allowed alternative source. Reviews are bound to
the dataset hash and exact answer hash. Re-score saved answers without regenerating:

```bash
python src/release_eval.py --results output/release_bench.json --reviews /path/to/reviews.json
```

Missing/stale reviews, unapproved labels/thresholds, incomplete results, lost comparison
intent, rejected responses, or failed quality thresholds block release. An all-abstaining
system cannot pass required-fact coverage. Empty scored categories never receive a
perfect score. The existing keyword benchmark is diagnostic only; its `correct` label
is not a production signoff.

## Exact generation and live checks

Build with `python src/build.py` before evaluation. New processes resolve
`output/current` to one immutable generation. Reports live inside that resolved
directory: use its actual `release_bench.json` path when rescoring. The legacy
`output/release_bench.json` command above applies only to legacy or scratch output.
Keep the dataset hash, answer hashes and generation manifest together during review.
Restart the service deliberately after promoting a new generation.

`CHIPVER_LIVE_TESTS=1 pytest -q tests/test_live_pipeline.py` runs real cached models
and local Ollama without the unit tagger/tokenizer substitutes. It checks startup
and source validation; it does not approve answer relevance or held-out labels.
