# Next steps

Handoff from the 2026-09-21/23 simplification sessions. Decided specs and execution queues:
[`docs/specs/ingest.md`](specs/ingest.md), [`docs/specs/spec_signals.md`](specs/spec_signals.md).
Deferred options: [`docs/DEFERRED.md`](DEFERRED.md) (DF-01 to DF-07).

## Where we are

- Branch `simplify`. Baseline commit `f1a2982`; step 1 committed as `d02e583`.
- MLflow runs:
  - `v0-baseline` (`a6154dc25d0242d38b72111e04802599`, `git_dirty=False`): 5559 chunks,
    2358 table rows, 65 unresolved table refs, 1088 signals, spec `family_recall_at_k` 0.623,
    routing accuracy 1.0.
  - `ingest/caption-regex` (`4bdb9fbc50e240ed8126669afea769d2`): 5575 chunks, 2471 table
    rows, 37 unresolved table refs, 1100 signals, `family_recall_at_k` 0.623 (unchanged),
    routing 1.0. Tagged `git_dirty=True` only because `NEXT_STEPS.md` was untracked; code
    was at `d02e583`.
- `spec_signals.py` walked (2026-09-23): spec written, nothing executed yet.

## Rhythm for every step

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests   # failing test first, then green
git commit ...                                             # one step per commit
.venv/bin/python src/build.py --clean                      # ~3 min, needs the GPU
.venv/bin/python src/bench.py --retrieval
.venv/bin/python src/track.py --tag <step tag>
.venv/bin/mlflow ui --backend-store-uri sqlite:///mlflow.db  # compare runs
```

Decision metric until step 0b lands: `retrieval.spec.family_recall_at_k`.

## Immediate next steps

1. **spec_signals s0 `signals/metrics`** and **s1 `signals/drop-dead-fields`** (zero-diff,
   run now; see the spec's queue).
2. **Open question:** un-ignore the gold set (`!eval/question_set.json` in `.gitignore`).
   Recommended; include it in step 0b.
3. **Ingest step 0b `eval/gold-sections`:** section + page labels for gold spec chunk IDs;
   section-level recall in `bench.py`. Must run before ingest step 9.
4. **Ingest steps 2-15** (including new 12b `ingest/wrapped-cells`), in order.
5. **spec_signals s2-s7**, after the ingest queue, so row deltas are attributable.

## After spec_signals

- Walk the next file in pipeline order (`build.py` stages: `embed.py`), same method.
- Pending consolidation at the `bridge` / `agent` walk: consumers move to `hierarchy_id` +
  `section_path`, `name`, raw `source` and `width`; then drop `section_id`,
  `section_title`, `subsection_id`, `subsection_title`, `id`, `original_name`, `row_kind`,
  `direction`, `source_role`, `source_role_source`.
- Open duplicate to decide at consumer walk: `doc_name` and `source_file` hold the same
  filename.
- Document authority (kept by decision): revisit form when `retrieve.py` is walked.
