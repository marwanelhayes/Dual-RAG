# Next steps

Handoff from the 2026-09-21/24 simplification sessions. Method and session prompt:
[`docs/SIMPLIFICATION_METHOD.md`](SIMPLIFICATION_METHOD.md). Decided specs and queues:
[`docs/specs/ingest.md`](specs/ingest.md), [`docs/specs/spec_signals.md`](specs/spec_signals.md),
[`docs/specs/embed.md`](specs/embed.md), [`docs/specs/rtl_ingest.md`](specs/rtl_ingest.md),
[`docs/specs/rtl_embed.md`](specs/rtl_embed.md), [`docs/specs/bridge.md`](specs/bridge.md).
Deferred options: [`docs/DEFERRED.md`](DEFERRED.md) (DF-01 to DF-19).

## Where we are

Branch `simplify`, head `1d4b526`. Tests: 257 passed. Decision metrics:
`retrieval.spec.section_recall_at_k` (spec) and `retrieval.rtl.exact_id_recall_at_k` (RTL, since
the `rtl_embed` walk).

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
| ingest 9 `position-sections` | `e29b845` | `83c7ca01` | 0.625 | boundaries correct; see below |
| ingest 9b `drop-heading-block` | `ab0d479` | `6e60695d` | 0.633 | 1432 headings out of text |
| embed e0 `move-shared-helpers` | `e5712c4` | `10478889` | 0.633 | zero diff; stage import-order test |
| embed e1 `drop-smoke-query` | `8cbe025` | `d6e6f1c5` | 0.633 | zero diff |
| embed e2 `drop-dedup` | `20be791` | `6a4d6155` | 0.633 | zero diff |
| embed e3 `text-manifest` | `1424b58` | `624ee454` | 0.633 | zero diff; main no longer writes to default paths |
| embed e4 `drop-normalize-knob` | `f130141` | `c75cefc5` | 0.633 | zero diff |
| embed e5 `chroma` | `0521d80` | `ec0aa075` | 0.633 | zero diff on all 60 questions; index 18 MB → 56 MB |
| rtl r0 `metrics` | `4948e96` | `a9576171` | 0.633 | rtl.* counts + 4 rates (all resolution rates 1.0) |
| rtl r1 `drop-aliases` | `63b1bd0` | `b4202fea` | 0.633 | rtl.json = old minus keys; schema 4 |
| rtl r2 `drop-compiler-internals` | `81ff2c2` | `6499c7a0` | 0.633 | rtl.json = old minus keys |
| rtl r3 `hierarchy-once` | `4c3f869` | `11ff82ba` | 0.633 | rtl.json = old minus keys; 4.1 → 3.4 MB |
| rtl r4 `drop-clock-reset` | `0d124ac` | `1a02e5ef` | 0.633 | rtl.json = old minus keys |
| rtl r5 `drop-content-hash` | `71e2a3a` | `4515b440` | 0.633 | rtl.json = old minus keys; 3.3 MB |

RTL steps (`rtl_embed` walk). Columns: spec section recall / RTL exact-id recall / RTL MRR;
RTL section recall stayed 1.0 throughout.

| Step | Commit | MLflow run | spec / rtl exact / mrr | Notes |
|---|---|---|---|---|
| rt0 `rtl_embed/metrics` | `b723512` | `a0235796` | 0.633 / 0.400 / 0.584 | baseline; line coverage 0.53 |
| rt1 `move-retrieval` | `903138e` | `9bdee203` | 0.633 / 0.400 / 0.584 | zero diff |
| rt2 `drop-dead-code` | `f4ba805` | `cc9904a3` | 0.633 / 0.400 / 0.584 | zero diff |
| rt3 `drop-fallbacks` | `1581a22` | `4b00c7bc` | 0.633 / 0.400 / 0.584 | zero diff |
| rt4 `drop-dead-fields` | `c7f9083` | `09e867c2` | 0.633 / 0.400 / 0.584 | rtl_chunks = old minus 12 keys |
| rt5 `rtl/relative-paths` | `910eadc` | `a499c8a3` | 0.633 / 0.400 / 0.584 | path values only |
| rt6 `instances-only` | `9ec48df` | `826890c7` | 0.633 / 0.392 / 0.542 | see below |
| rt7 `wiring-directions` | `8a95eaf` | `a2d7f4ab` | 0.633 / 0.433 / 0.610 | 25 module texts |
| rt8 `drop-repeated-facets` | `6e3d94b` | `0de7b7dd` | 0.633 / 0.417 / 0.627 | see below |
| rt9a `rtl/preamble` | `aded00e` | `186d454c` | 0.633 / 0.417 / 0.627 | rtl.json = old plus key |
| rt9b `preamble-comments` | `3a55661` | `46a6f153` | 0.633 / 0.425 / 0.596 | 26 module texts |
| rt10 `windows` | `2d52910` | `1f036772` | 0.633 / 0.458 / 0.634 | coverage 1.0; 308 records |
| rt11 `drop-terms-cap` | `1d4b526` | `939a6415` | **0.633 / 0.458 / 0.625** | 323 records; max 474 tokens |

rt6 (-1 gold id): `cache` now names both parents, which trimmed its comment facet; its vector
moved and `CMP_EXCLUSIVE_EXACT` lost `rtl:cache` at rank 5. rt8 (-2): all 154 texts changed;
3 of 5 lost ids were trimmed blocks shifting among near ties (3 gained, MRR up). Both were
trim effects that rt10 removed.

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

## Step 9 / 9b outcome

Step 9 cut sections at outline points (0.640 → 0.625); 9b (option A, 2026-09-23) drops the
first block on a section's start page when it equals the outline title, so the heading is
not repeated after the breadcrumb (0.625 → 0.633). A blind first-block drop was rejected: 3
test PDFs have points but no printed heading, and it would delete body text.

- `CMP_MINFO_CSR_EXACT` back to 1.0.
- `SPEC_AXI_WLAST` still 0: waveform 7.6 outranks spec A3.2.1. Waveform headings stay in
  text (a copyright line precedes them, 54 sections), so 9b does not touch that PDF.

## Then

Start the next session with `/grill-me` and the prompt in `docs/SIMPLIFICATION_METHOD.md`,
ending "... going for bridge.py" when the walk queue reaches it.

1. Ingest steps 10-15 in `docs/specs/ingest.md` (10 prose excludes tables, 11 table owner by
   position, 12 continuations, 12b wrapped cells, 13 code-line split, 14 token caps, 15
   sweep). Step 13 uses the newlines step 9 keeps; step 14 also closes DF-09 (52 chunks over
   512 tokens).
2. spec_signals s2-s7 after the ingest queue (row deltas attributable).
3. Then `bridge.py`, `retrieve.py` / `agent.py` walks.

Done 2026-09-23: step 9b, `embed.py` walk + e0-e5 (Chroma), `rtl_ingest.py` walk + r0-r5.
Done 2026-09-24: `rtl_embed.py` walk + rt0-rt11. Specs: [`embed.md`](specs/embed.md),
[`rtl_ingest.md`](specs/rtl_ingest.md).

## Pending consolidation (bridge / agent walk)

Consumers move to `hierarchy_id` + `section_path`, `name`, raw `source` and `width`; then
drop `section_id`, `section_title`, `subsection_id`, `subsection_title`, `id`,
`original_name`, `row_kind`, `direction`, `source_role`, `source_role_source`. Open
duplicate: `doc_name` and `source_file` hold the same filename. Document authority (kept)
is revisited at the `retrieve.py` walk.

## Pending consolidation (embed / rtl, by consumer walk)

- `rtl_embed`: done (rt0-rt11). Alias pairs `file` / `file_path`, `line` / `start_line`, `symbol_name` /
  `original_name`, `instance_id` / `construct_id` (also bridge, agent).
- `bridge`: `enum_usage` moves here or is dropped; `naming.py` judged here.
- `retrieve`: `langchain_core.documents.Document` as hit type; `VectorStore.load(records)`.
- `agent`: the embed manifest (freshness guard) is revisited.

## Known issues noted, not fixed

- `rtl_ingest._module_extents` ends a module at the next module's start, so the second
  module in a file (`booth_multiplier_seq`, `lzc`, `non_restoring_divider_seq`) has an empty
  `preamble` and its header comment is embedded in the previous module's comments.
- `rtl.construct.chunk_tokens_max` is 474 of 512 with `RTL_CHUNK_CHARS = 1200`; windows cut
  by characters, so a denser block could pass 512 (`rtl.chunks_over_limit` watches it).

- `naming._raw_tokens` splits `ARESETn` as `arese` + `tn` (acronym boundary), so AXI-style
  `…n` resets cannot link spec to RTL. Fix at the `bridge.py` walk, failing test first.
- `rtl_ingest.enum_usage`: 13 of 326 usage sites match an enum member only inside comments.

- `bench._chunk_family` treats an unwindowed table's label as a window suffix, so
  `family_recall_at_k` counts any table of the document as a hit (superseded as a decision
  metric by section recall).
- `SPEC_AXI_WLAST` is 0 since step 9: waveform 7.6 outranks spec A3.2.1.
- `faiss-cpu` and `langchain-community` left `requirements.txt` but are still installed in
  `.venv`; `pip uninstall` them when convenient.
- `.gitignore` is itself ignored, so un-ignore lines are local only; tracked eval files were
  added with `git add -f`.
