# Next steps

Handoff from the 2026-09-21/22 simplification session. The decided ingest spec and full
execution queue live in [`docs/specs/ingest.md`](specs/ingest.md); deferred options in
[`docs/DEFERRED.md`](DEFERRED.md).

## Where we are

- Branch `simplify`, baseline commit `f1a2982` (pre-simplification tree + `src/track.py`).
- MLflow run `v0-baseline` logged (`a6154dc25d0242d38b72111e04802599`, `git_dirty=False`):
  5559 chunks, 56 over 512 tokens, prose p50/p95/max 201/330/2597 tokens, 1507 prose
  chunks with running-header leaks, 2358 table rows, 65 unresolved table refs, 1088
  signals, spec `family_recall_at_k` 0.623, routing accuracy 1.0.
- **Uncommitted in the working tree:** the caption regex fix (step 1) in `src/ingest.py` and
  its 3 tests in `tests/test_spec_ingest.py`, plus a metric-note edit in
  `docs/specs/ingest.md`. Full suite: 227 passed.

## Rhythm for every step

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests   # failing test first, then green
git commit ...                                             # one step per commit
.venv/bin/python src/build.py --clean                      # ~3 min, needs the GPU
.venv/bin/python src/bench.py --retrieval
.venv/bin/python src/track.py --tag <step tag>
.venv/bin/mlflow ui --backend-store-uri sqlite:///mlflow.db  # compare runs
```

Decision metric until step 0b lands: `retrieval.spec.family_recall_at_k`. Every other
retrieval metric is scored on chunk IDs and breaks when chunking renumbers them.

## Immediate next steps

1. **Open question:** un-ignore the gold set (`!eval/question_set.json` in `.gitignore`) so
   the labels behind every run's numbers are versioned. Recommended; include it in step 0b.
2. **Step 0b `eval/gold-sections`:** from the baseline `output/chunks.jsonl`, add a
   `(document_id, section_number or section name, page)` label for every expected spec chunk
   ID in `eval/question_set.json`; add section-level recall to `bench.py`. Must run before
   step 9, which renumbers chunk IDs.
3. **Step 1 `ingest/caption-regex`:** commit the working-tree fix, rebuild, track. Expect
   +16 RISC-V tables (priv 51-53; unpriv 42, 44, 45, 47-49, 51-54, 57-59), 0 AXI change,
   fewer unresolved table refs, more signals.
4. **Steps 2-15:** follow the queue in `docs/specs/ingest.md`, in order: zero-diff removals
   (2-6), fixes (7-13), token caps (14), then the `sweep/chunk_tokens` grid (15).

## After ingest

- Walk `src/spec_signals.py` next (pipeline order), same method: spec first, remove-first,
  every decision measured as an MLflow run. Write `docs/specs/spec_signals.md`.
- Pending consolidation when `spec_signals`, `agent`, `bridge` are walked: consumers move
  to `hierarchy_id` + `section_path`; then drop `section_id`, `section_title`,
  `subsection_id`, `subsection_title`.
- Open duplicate to decide at consumer walk: `doc_name` and `source_file` hold the same
  filename.
- Document authority (kept by decision): revisit form when `retrieve.py` is walked; four
  documents may not need a regex classifier.
