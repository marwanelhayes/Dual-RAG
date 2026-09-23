# Next steps

Handoff from the 2026-09-21/23 simplification sessions. Method and session prompt:
[`docs/SIMPLIFICATION_METHOD.md`](SIMPLIFICATION_METHOD.md). Decided specs and queues:
[`docs/specs/ingest.md`](specs/ingest.md), [`docs/specs/spec_signals.md`](specs/spec_signals.md).
Deferred options: [`docs/DEFERRED.md`](DEFERRED.md) (DF-01 to DF-07).

## Where we are

Branch `simplify`, head `e29b845`. Decision metric: `retrieval.spec.section_recall_at_k`.

| Step | Commit | MLflow run | section recall | Notes |
|---|---|---|---|---|
| `v0-baseline` | `f1a2982` | `a6154dc2` | - | 1088 signals, 1507 header leaks |
| ingest 1 `caption-regex` | `d02e583` | `4bdb9fbc` | - | +113 table rows, unresolved refs 65 → 37 |
| signals s0 `signals/metrics` | `d929ba1` | `a54ac728` | - | AXI channel recall 0.994 (153/154) |
| signals s1 `drop-dead-fields` | `2c6630e` | `bb037dae` | - | zero diff |
| ingest 0b `eval/gold-sections` | `f0f34c8` | `e6208d6f` | 0.615 | gold set now versioned |
| ingest 2 `drop-langchain-splitters` | `3e74cf7` | `fdb9557d` | 0.615 | chunks byte-identical |
| ingest 3 `drop-dead-fields` | `9819c28` | `59c95467` | 0.615 | 13 fields, chunks.jsonl -32% |
| ingest 4 `remove-chunk-roles` | `880dd66` | `58a147b6` | 0.615 | leading_role also removed |
| ingest 5 `drop-tables-cited` | `2408821` | `b2e75114` | 0.615 | zero content diff |
| ingest 6 `header-no-revision` | `59b21a3` | `bc3398aa` | 0.631 | MRR 0.552 → 0.561 |
| ingest 7 `furniture-bands` | `feae05b` | `d8a57cc6` | 0.640 | header leaks 1507 → 0; 7b skipped |
| test fix (gold by section) | `ff6013e` | - | - | re-chunking dropped 2 of 83 gold IDs |
| ingest 8 `fallback-validation` | `cfc2deb` | `de8ec716` | 0.640 | fallback F1 0.61-0.73 |
| ingest 9 `position-sections` | `e29b845` | `83c7ca01` | **0.625** | **open decision below** |

Step 7b (`drop-text-furniture`) is skipped by its own rule: `furniture_text_only_hits` is
not 0 (AXI 321, waveform 125, riscv-privileged 266: stacked copyright footers above the
outermost band block), so `repeated_lines` stays.

Dirty duplicate runs left in MLflow (same tags, superseded by clean ones): `9479932a`
(s1), `0643db4a` (0b). Delete on request.

## Rhythm for every step

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests   # failing test first, then green
git commit ...                                             # one step per commit
.venv/bin/python src/build.py --clean                      # ~3 min, needs the GPU
.venv/bin/python src/bench.py --retrieval
.venv/bin/python src/track.py --tag <step tag>
.venv/bin/mlflow ui --backend-store-uri sqlite:///mlflow.db  # compare runs
```

Run these in the foreground; never edit while they run (`track.py` records `git_dirty`).

## Open decision: step 9 lowered section recall 0.640 → 0.625

Per-question diff (from the `de8ec716` `retrieval_bench.json` artifact): two questions.

- `SPEC_AXI_WLAST` 0.25 → 0: retrieval now prefers waveform 7.6 over spec A3.2.1.
- `CMP_MINFO_CSR_EXACT` 1.0 → 0.67: lost the 20-chunk generic `2.2 CSR Listing` from top 5.

Step 9's boundaries are more correct: before it, `3.1.3 marchid` began with the tail of the
previous `misa` section (7470 chars, now 2789). Likely cost: each section's heading line now
opens its text and merges into the first sentence ("A3.2.1 Write data channel (W) The data
and last signals ...").

Options (recommended first):

- **(A)** Keep step 9; run experiment **9b `ingest/drop-heading-block`**: drop the first
  block at or after each section's destination point on its start page (position-based, no
  title search). Keep 9b only if section recall rises.
- (B) Revert step 9.
- (C) Keep step 9, accept -0.015.

## Then

1. Ingest steps 10-15 in `docs/specs/ingest.md` (10 prose excludes tables, 11 table owner by
   position, 12 continuations, 12b wrapped cells, 13 code-line split, 14 token caps, 15
   sweep). Step 13 uses the newlines step 9 now keeps.
2. spec_signals s2-s7 after the ingest queue (row deltas attributable).
3. Walk `embed.py` next (`build.py` stage order) with the method doc's prompt.

## Pending consolidation (bridge / agent walk)

Consumers move to `hierarchy_id` + `section_path`, `name`, raw `source` and `width`; then
drop `section_id`, `section_title`, `subsection_id`, `subsection_title`, `id`,
`original_name`, `row_kind`, `direction`, `source_role`, `source_role_source`. Open
duplicate: `doc_name` and `source_file` hold the same filename. Document authority (kept)
is revisited at the `retrieve.py` walk.

## Known issues noted, not fixed

- `bench._chunk_family` treats an unwindowed table's label as a window suffix, so
  `family_recall_at_k` counts any table of the document as a hit (superseded as a decision
  metric by section recall).
- `.gitignore` is itself ignored, so un-ignore lines are local only; tracked eval files were
  added with `git add -f`.
