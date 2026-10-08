# Next steps

Handoff from the 2026-09-21/24 simplification sessions. Method and session prompt:
[`docs/SIMPLIFICATION_METHOD.md`](SIMPLIFICATION_METHOD.md). Decided specs and queues:
[`docs/specs/ingest.md`](specs/ingest.md), [`docs/specs/spec_signals.md`](specs/spec_signals.md),
[`docs/specs/embed.md`](specs/embed.md), [`docs/specs/rtl_ingest.md`](specs/rtl_ingest.md),
[`docs/specs/rtl_embed.md`](specs/rtl_embed.md), [`docs/specs/bridge.md`](specs/bridge.md),
[`docs/specs/retrieve.md`](specs/retrieve.md), [`docs/specs/agent.md`](specs/agent.md).
Deferred options: [`docs/DEFERRED.md`](DEFERRED.md) (DF-01 to DF-32).

## Where we are

Branch `simplify`, head `cff008c` + docs (2026-10-08 section below). Tests: 273 passed. Decision metrics:
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

Queues run 2026-09-24 (columns: spec section recall / RTL exact-id recall; bridge recall /
precision against `eval/bridge_gold.json`, 25 AXI channel signals).

| Step | Commit | MLflow run | spec / rtl | Notes |
|---|---|---|---|---|
| ingest 10 `prose-excludes-tables` | `1cd7264` | `b00eb79a` | 0.608 / 0.458 | prose with a table header 404 → 59 |
| ingest 11 `table-owner-position` | `423ba04` | `f47dbba8` | 0.617 / 0.458 | 127 table owners corrected |
| ingest 12 `table-continuations` | `3d059b4` | `8b9356ce` | 0.617 / 0.458 | table rows 2471 → 3109 |
| ingest 12b `wrapped-cells` | `5952227` | `fcee13b7` | 0.617 / 0.458 | rows 3109 → 2964 (run logged pre-amend; comment-only diff) |
| ingest 13 `code-line-split` | `f24e832` | `5f70353f` | 0.617 / 0.458 | chunks over 512 tokens 38 → 21 |
| ingest 14 `token-caps` | `965d084` | `43d18285` | 0.583 / 0.458 | 0 over limit; hard limit fails the run |
| ingest 15 `sweep/chunk_tokens` | `68011aa` | `b0eb5072` | **0.675** / 0.458 | 9-point grid; prose 384 / table 128 tokens |
| signals s2-s7 | `d4265ea`..`09150b0` | `e2bae491`..`311a6295` | 0.675 / 0.458 | rows 1557 → 931, ids unique |
| bridge b0 `metrics` | `01d7147` | `062b11bb` | bridge 0.44 / 1.0 | 13 links |
| bridge b1-b4 removals | `bf84696`..`dbc296e` | `2135c733`..`306f2d50` | bridge 0.44 / 1.0 | zero / minus-key diffs |
| bridge b5 `identity-links` | `57e637e` | `ff227735` | bridge **1.0** / 0.972 | 569 links; learned prefixes |
| bridge b6 `same-net` | `7fe75f0` | `54758a49` | bridge 1.0 / 0.94 | 19 net links, all spreading name collisions |
| bridge b7-b9 | `2768d78`..`45fe9fb` | `a9619356`..`8aa7cd87` | bridge 1.0 / 0.94 | suggestions; 18 CSR→enum links; module links |
| bridge b10 `consolidate-signals` | `b68ccf3` | `07e50ab5` | bridge 1.0 / 0.94 | spec_signals contract; modules 136 |
| naming `active-low-suffix` | `6545463` | `c8f6267f` | 0.675 / 0.458 | `ARESETn` → `areset` + `n` |

Retrieve queue run 2026-09-26 (stage-local metrics from q0). Columns: agent spec section / agent
RTL exact / stage spec section / stage RTL exact / stage RTL MRR. Retrieve-only steps skip the
build (artifacts unchanged); q0 and q7 rebuilt.

| Step | Commit | MLflow run | agent spec / rtl · stage spec / rtl / mrr | Notes |
|---|---|---|---|---|
| q0 `retrieve/metrics` | `690f9e1` | `d4899e7a` | 0.675 / 0.458 · 0.683 / 0.508 / 0.630 | baseline |
| exp `q-exp/no-rerank` | `e26678f` (branch `exp/no-rerank`) | `5e39a486` | 0.493 / 0.425 · 0.493 / 0.500 / 0.608 | reranker kept |
| exp `q-exp/no-owned` | branch `exp/no-owned` | `ce9bcc2b` | 0.675 / 0.458 · 0.683 / 0.475 / 0.616 | owned rerank kept |
| q1 `retrieve/drop-dead-code` | `bcf9a76` | `43c36d90` | 0.675 / 0.458 · 0.683 / 0.508 / 0.630 | zero diff |
| q2 `retrieve/union-not-rrf` | `fc4f0b1` | `7d845943` | 0.675 / 0.458 · 0.683 / 0.508 / 0.630 | zero diff |
| q3 `retrieve/drop-spec-exact-lane` | `833f040` | `9812f196` | 0.683 / 0.458 · 0.683 / 0.508 / 0.630 | 6 spec lists reordered |
| q4 `retrieve/case-exact-tiers` | `556f196` | `8916fefd` | 0.683 / 0.467 · 0.683 / 0.508 / 0.649 | |
| q5 `retrieve/query-terms` | `babee85` | `32152773` | 0.683 / 0.467 · 0.683 / 0.508 / 0.649 | zero diff |
| q6 `retrieve/one-ordering` | `6b1fcf8` | `f8f6b192` | 0.683 / **0.508** · 0.683 / **0.517** / 0.649 | TRAP_ENTRY 1/3 → 2/3 |
| q7 `rtl_embed/lookup-fields` | `705ffbc` | `f45a7460` | 0.683 / 0.500 · 0.683 / 0.508 / 0.649 | -1 gold id, accepted (DF-25) |
| q8 `retrieve/hit-shape` | `03d2903` | `b446c527` | **0.683 / 0.500** · **0.683 / 0.508 / 0.649** | zero diff |

q7 (-1 gold id): `CMP_MINFO_CSR_EXACT` ("…implemented in csr_file?") lost
`rtl:construct:csr_file:task:83-103`. Before q7 the signal `CsrFile` matched through its
`normalized_name` `csr_file` (case-exact against the query word), making `rtl:csr_file` a
signal hit whose structures included the gold task. `signal_names` holds only original names.
The walk's Q11 finding ("retrieve reads only `original_name`") missed that q4 still matched
signal `normalized_name`. Decided 2026-09-26: accept the loss (DF-25).

Agent queue run 2026-09-26. Columns: context spec section / context RTL exact / mean context
chars (decision: `retrieval.context.*`, gold inside `citable_evidence`); agent@5 stayed 0.683 /
0.500-0.508.

| Step | Commit | MLflow run | context spec / rtl / chars | Notes |
|---|---|---|---|---|
| a0 `agent/metrics` | `4ee7f5c` | `533d7f56` | 0.575 / 0.308 / 9.2k | baseline |
| a1 `agent/drop-recovery` | `f7dd3b7` | `d9c659aa` | 0.575 / 0.308 / 9.2k | zero diff |
| a2 `agent/drop-dead-code` | `cae5694` | `f63b873c` | 0.575 / 0.308 / 9.2k | zero diff |
| a3 `agent/drop-hierarchy-expansion` | `4eac585` | `12cd04b9` | 0.575 / 0.308 / 9.2k | |
| a4 `agent/drop-rtl-blocks` | `d2ca848` | `4e2d6b67` | 0.575 / **0.508** / 9.2k | 13 questions gain |
| a5 `agent/drop-reverse-requery` | `5f783ae` | `eb73a010` | 0.575 / 0.508 / 9.2k | |
| a6 `agent/drop-lane-floors` | `61dfd26` | `8bd8123a` | 0.575 / 0.525 / 10.4k | linear graph |
| a7 `agent/whole-passages` (5/5) | `b7f8ba5` | `69a8e6b4` | **0.683** / 0.508 / 11.8k | RTL -0.017: sixth construct no longer fits |
| a8 `agent/drop-signal-rows` | `f4aaf03` | `bf3105c5` | 0.683 / 0.508 / 11.6k | |
| a9 `sweep/agent_k` (8/8) | `0921811` | `1bb31243`, `5dba40b2`, `24505982` (5/8, 8/5, 8/8) | **0.748 / 0.600** / 17.9k | max ~5.9k bge tokens |
| a10 `agent/bridge-schema-keys` | `05b1d33` | `4458a76f` | 0.748 / 0.600 / 17.9k | |
| a11 `agent/rtl-chunks` | `9e90e2a` | `025bcf91` | 0.748 / 0.600 / 17.9k | zero diff |
| a12 `ingest/drop-agent-section-fields` | `ac9d39e` | `80b73054` | 0.740 / 0.600 / 17.8k | minus keys proved; -1 passage is index-build noise |
| noise `noise/embed-rebuild` | `ac9d39e` | `3290cbce` | 0.740 / 0.600 / 17.8k | same inputs, rebuilt index |

a12: chunks and signals equal the old minus the dropped keys, yet rank 8 changed in 3 AXI
questions. Rebuilding the spec index from identical chunks changes the dense top-30 (order and
set), so index construction is non-deterministic (Chroma HNSW or GPU embedding); tail ranks move
between builds. Retrieval@5 and stage metrics were identical across the two rebuilds.

Ingest 10 (-0.025): gold rewarded prose copies of the rounding-mode table; the table chunk is
still retrieved. Ingest 14 (-0.034): the default 256-token cap, fixed by the sweep.

rt6 (-1 gold id): `cache` now names both parents, which trimmed its comment facet; its vector
moved and `CMP_EXCLUSIVE_EXACT` lost `rtl:cache` at rank 5. rt8 (-2): all 154 texts changed;
3 of 5 lost ids were trimmed blocks shifting among near ties (3 gained, MRR up). Both were
trim effects that rt10 removed.

Step 7b (`drop-text-furniture`) is skipped by its own rule: `furniture_text_only_hits` is
not 0 (AXI 321, waveform 125, riscv-privileged 266: stacked copyright footers above the
outermost band block), so `repeated_lines` stays.

Dirty duplicate runs left in MLflow (same tags, superseded by clean ones): `9479932a`
(s1), `0643db4a` (0b). Delete on request.

## 2026-10-08: field-generic code, gold relabel, reranker

Goal 1 removed corpus-specific code (user rule: code may know hardware/Verilog/SystemVerilog,
never these PDFs, this core, or the eval questions). Goal 2 measured accuracy changes; a drop
on either decision metric reverted the change.

| Step | Commit | MLflow run | spec section / RTL exact | Notes |
|---|---|---|---|---|
| `rtl_ingest/detect-top` | `3779979` | `34292cfc` | 0.683 / 0.508 | top + packages from sources; `rtl.json` byte-identical |
| `agent/design-names-route` | `02c9657` | `15a1ba5c` | 0.683 / 0.508 | identifiers from `rtl.json`; no axi/risc-v/timer keywords |
| `ingest/caption-styles` | `cbfe802` | `5de3fe4d` | 0.683 / 0.508 | caption/continuation styles; chunks byte-identical |
| `rtl_embed/encoder-guard` | `619ff14` | `929b9f38` | 0.683 / 0.508 | superseded by `no-rtl-index` |
| `rtl_ingest/verilog-sources` | `d8a4d69` | `c1fdd0eb` | 0.683 / 0.508 | `.v`/`.svh`/`.vh` read |
| `naming/drop-ambiguous-wb` | `d2abfc6` | `ac143586` | 0.683 / 0.508 | RTL context 0.600 → 0.592 |
| `naming/drop-ambiguous-rnd` | `19e0d88` | `e7c021e5` | 0.683 / 0.508 | unused here |
| `eval/relabel-gold` | `374b44d` | `d1f783b4` | 0.683 / 0.508 | 42 stale spec ids remapped; spec exact 0.290 → 0.583 |
| `retrieve/bm25-trailing-dot` | `89ea6cf` | `28430af7` | 0.675 / 0.500 | reverted `80ae0ba` (`98e3ee74`) |
| `retrieve/candidates-60` | `957ea2d` | `67dc26f1` | 0.667 / 0.500 | reverted `4f18a86` (`95dd9a43`) |
| `retrieve/bge-reranker-v2-m3` | `6cc622c`, `b04dc40`, `cfa7f45` | `2d31c3c1` | 0.683 / **0.558** | shared + fp16 to fit 4 GB; spec MRR 0.590 → 0.542; 0.9 → 3.2 s/q |
| `retrieve/rtl-lexical-only` | `733c3fb` | `c3ad224d` | 0.683 / **0.575** | RTL MRR 0.720; 2.8 s/q |
| `rtl_embed/no-rtl-index` | `116ac78` | `68c40dd5` | 0.683 / 0.575 | RTL index, manifest, guard removed |
| `retrieve/rerank-logits` | `cff008c` | `65447704` | **0.698** / 0.575 | v2-m3's sigmoid head let the authority shift outweigh relevance; raw logits: spec MRR 0.542 → 0.605, spec exact 0.604 |

Scratch-only (not committed): RTL module description in the header (RTL MRR 0.761, RTL exact
0.558) and table rows as sentences (spec section 0.667) both failed the decision rule.
Diagnostic: the candidate union holds 87% of spec and 90% of RTL gold, top 5 holds 65% / 47%
with MiniLM; ranking, not recall, is the bottleneck. EmbeddingGemma 2 sweep (512-8192
windows): worse than BGE at every window; dense-only RTL recall 0.208 vs BGE 0.275.

Dirty duplicate runs (superseded by clean ones with the same tag): `5380d353`
(detect-top), `7125ee52` (relabel-gold); the four `sweep/gemma2 w*` runs are dirty only by
the untracked `video/`, now in `.git/info/exclude`.

Next:

1. Close the remaining rank gap (gold in pool, out of top 5) without per-corpus training; a
   reranker fine-tuned on synthetic questions from these PDFs needs a second spec/RTL pair to
   be judged without leakage.
2. Gold ids are tied to chunking: re-chunking re-strands exact ids (section recall survives).

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
ending "... going for retrieve.py".

1. **Decide index determinism** (`embed.py`): identical chunks give a different dense top-30
   after a rebuild. Find whether embeddings or HNSW construction differ, then fix or accept
   (e.g. exact search at 3.6k chunks) before trusting one-passage diffs.
2. **Answer bench** (Ollama `qwen3:4b`, `AGENT_NUM_CTX` 8192): never run on this branch; the
   agent now always calls the model when required domains are citable.
3. Walk `answer_contract.py`, `api.py`, `release_eval.py`, `bench.py` (not walked).
4. Decide `same_net` (bridge b6): it adds 19 links in this RTL, all spreading two exact-name
   collisions (`WDATA`→`WData` in the register files, `VALID`→`valid`). Options: keep, drop
   (DF), or propagate only from `interface_prefix` links.

Done 2026-09-23: step 9b, `embed.py` walk + e0-e5 (Chroma), `rtl_ingest.py` walk + r0-r5.
Done 2026-09-26: `retrieve.py` walk + q0-q8; `agent.py` walk + a0-a12.
Done 2026-09-24: `rtl_embed.py` walk + rt0-rt11; ingest 10-15; spec_signals s2-s7;
`bridge.py` walk + b0-b10; `ARESETn` tokenization; `faiss-cpu` / `langchain-community`
uninstalled from `.venv`.

## Pending consolidation (agent walk)

spec_signals still writes `section_id` / `subsection_id` because `agent.select_spec_signals`
matches them against chunk metadata; the agent moves to `hierarchy_id`, then both go (and
ingest's `section_id`, `section_title`, `subsection_id`, `subsection_title`). The agent's
`direction=` print reads a field that no longer exists. Open duplicate: `doc_name` and
`source_file` hold the same filename. Document authority (kept) is revisited at the
`retrieve.py` walk.

## Pending consolidation (embed / rtl, by consumer walk)

- `rtl_embed`: done (rt0-rt11). Alias pairs `file` / `file_path`, `line` / `start_line`, `symbol_name` /
  `original_name`, `instance_id` / `construct_id` (also bridge, agent).
- `retrieve`: `langchain_core.documents.Document` as hit type; `VectorStore.load(records)`.
- `agent`: the embed manifest (freshness guard) is revisited.

## Known issues noted, not fixed

- `rtl_ingest._module_extents` ends a module at the next module's start, so the second
  module in a file (`booth_multiplier_seq`, `lzc`, `non_restoring_divider_seq`) has an empty
  `preamble` and its header comment is embedded in the previous module's comments.


- `bench._chunk_family` treats an unwindowed table's label as a window suffix, so
  `family_recall_at_k` counts any table of the document as a hit (superseded as a decision
  metric by section recall).
- `SPEC_AXI_WLAST` is 0 since step 9: waveform 7.6 outranks spec A3.2.1.
- Bridge name collisions: exact identity links `WDATA` → register-file `WData` (9 rows) and
  `VALID` → FPU `valid` (6), counted wrong by `bridge.axi_precision`.
- `.gitignore` is itself ignored, so un-ignore lines are local only; tracked eval files were
  added with `git add -f`.
