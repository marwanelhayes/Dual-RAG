# Spec: `src/rtl_embed.py`

Decided 2026-09-24 in the simplification pass. Deferred options live in
[`docs/DEFERRED.md`](../DEFERRED.md) (DF-12 initial blocks, DF-13 package headers).

## Domain

`rtl_embed.py` projects `output/rtl.json` into RTL retrieval records
(`output/rtl_chunks.jsonl`), ranked by exact lookup, BM25, and the reranker; they are not
embedded (dense vectors lowered RTL recall, `retrieve/rtl-lexical-only`). Units: one module record and one record per
structural block (always, function/task, assignment group), each split into whole-line
windows when it exceeds the budget.

Non-goals: retrieval (exact lookup, `RTLRetriever`, reranking) moves to `retrieve.py` and is
judged at that walk; index freshness (`index_manifest_status`) moves to `embed.py`, beside
the manifest it reads; parsing SystemVerilog (`rtl_ingest.py` owns every source fact).

## Baseline (2026-09-24, head `d79b5e1`)

- 154 records: 35 module, 119 construct (55 `ALWAYS_COMB`, 20 `ALWAYS_FF`, 17
  `ASSIGNMENT_GROUP`, 17 `FUNCTION`, 9 `TASK`, 1 `PROCESS`).
- bge tokens (header + text): module median 351 / max 477, construct median 228 / max 468;
  none over 512. Budget `RTL_CHUNK_CHARS = 1200`.
- Coverage: 7672 module source lines, 4294 inside blocks, **1551 embedded as code** (36 of
  119 blocks trimmed). Comments cut in 16 of 35 module records; module header comments
  (above `module`, 33 files) never embedded.
- RTL bench (40 questions, k=5): section recall 1.0, MRR 0.584, **exact-id recall 0.4**.
  Gold constructs trimmed: 29, of which 23 missed (79%); untrimmed: ~35% missed.

## What rtl_embed must do

1. **Module record** `rtl:<module>`: facets in order `module X`, `instantiated by:` (every
   parent), `ports:`, `terms:`, `drives:`, `reads:`, `instantiates:`, `comments:` (module
   preamble first, then body; `//` and `/* */`, decoration stripped).
2. **Construct record** `rtl:construct:<module>:<kind>:<first>-<last>`: `reads:`, `writes:`,
   `terms:`, then the block source.
3. **Header** `RTL > rtl/<module>` or `RTL > rtl/<module> > <kind> lines a-b`; the text never
   repeats it.
4. **Windows**: a record over `RTL_CHUNK_CHARS` continues in `<chunk_id>:w1`, `:w2`, ...
   cut only between lines; a long facet wraps onto continuation lines that repeat its label,
   and a single line over the budget is split rather than dropped. Identity is not repeated
   in the text: the `context_header` (`RTL > rtl/<module>[ > <kind> lines a-b]`) is embedded
   with every window. The first window keeps the plain id and all metadata, so gold ids hold;
   continuation windows carry provenance only (no `lookup_terms`, `signal_metadata`,
   `signals_*`), so exact lookup sees each record once. `retrieve.RTLRetriever` folds `:wN`
   hits into one hit on the record, keeping the matched window text.
5. **Parents and children** come from `instances[*].target_module` only (tree children equal
   them in all 35 modules).
6. **Wiring** from port bindings sharing one parent net, by stated directions: `drives X ->
   peer` only to a peer whose formal is not `output`, `reads X <- peer` only from a peer whose
   formal is `output`.
7. **No render caps.**
8. **Fields read directly**; `rtl.json` schema 4 is the contract, no fallbacks.
9. `main` writes `rtl_chunks.jsonl`, the index, and the manifest; no smoke query, no skip.

Record fields kept: `chunk_id`, `context_header`, `text`, `record_id`, `module`, `file`,
`line`, `start_line`, `end_line`, `source_type`; for exact lookup until the retrieve walk:
`lookup_terms`, `normalized_name`, `original_name`, `signal_metadata`, `signals_read`,
`signals_written`.

## Removed

| Item | Why |
|---|---|
| `structure:` facet, `_MODULE_STRUCTURES` | line numbers; each block is its own record |
| `normalized module:` facet | equals the first `terms:` item in 33/33 |
| construct `{kind} in module {m}` and `source:` lines | repeat the header |
| `children()`, `child_modules` parameter, tree read | equals instance targets; inversion kept one parent (`risc_mux2`, `cache`, `lzc` wrong) |
| `reads` links to sibling readers | 170 of 338 `reads` pairs were not drivers |
| trimming in `_budgeted_facets`, `_FACET_MIN_CHARS`, `_whole_lines` trim | windows instead |
| `_MODULE_PORTS`, `_MODULE_LINKS`, `_CONSTRUCT_SIGNALS` | hid 14 modules' ports |
| `_comment_parts`, `STRUCTURAL_SURFACE_CHARS`, `cfg.RTL_CHUNK_COMMENT_CHARS` | no callers |
| fallbacks (`source_text`, `kind`, `symbol_name`/`name`, `line`, disk re-read, ...) | 0 fires on schema 4 |
| fields `content_hash`, `source_id`, `domain`, `module_content_hash`, `compiler_content_hash`, `clock`, `reset`, `construct_name`, `sensitivity`, `construct_type`, `source_code`, `normalized_tokens` | 0 readers |
| `main` smoke query, skip-if-fresh | mirrors embed e1 / e3 |

Moved: retrieval block (`_IDENTIFIER`, `_QUERY_STOP_WORDS`, `_query_terms`, `_term_strength`,
`_phrase_strength`, `exact_lookup`, `RTLRetriever`, `search`, `load_records`) →
`retrieve.py`; `index_manifest_status` → `embed.py`.

Added: `rtl_ingest` module field `preamble` (source lines above `start_line`, back to the
previous module's end or the file start).

## Pending consolidation

- `agent.py:1301,1341` rebuild records from `rtl.json`; switch to `rtl_chunks.jsonl` at the
  agent walk.
- Exact-lookup fields (`lookup_terms`, `signal_metadata`, `normalized_name`, ...) and
  `_LOOKUP_TERMS` are judged at the retrieve walk.
- `line` / `start_line` alias until the agent walk.
- `terms:` depends on `naming.py` aliases (judged at the bridge walk).

## Metrics (`track.py`)

Decision: `retrieval.rtl.exact_id_recall_at_k` (0.4). Guards: `retrieval.rtl.section_recall_at_k`
1.0, `retrieval.spec.section_recall_at_k` 0.633. Added at rt0: `rtl.records.module`,
`rtl.records.construct`, `rtl.embedded_line_coverage` (block lines embedded as code / block
lines), `rtl.chunks_over_limit` (> 512 bge tokens).

## Execution queue

Every step: failing test first, full suite green, commit, `build.py --clean`,
`bench.py --retrieval`, `track.py --tag <tag>`. Removal steps prove `rtl_chunks.jsonl` new =
old minus keys.

| Step | Tag | Change | Expected diff |
|---|---|---|---|
| rt0 | `rtl_embed/metrics` | record counts, line coverage, over-limit | metrics only |
| rt1 | `rtl_embed/move-retrieval` | retrieval → `retrieve.py`, `index_manifest_status` → `embed.py`, 12 tests move, agent imports | zero |
| rt2 | `rtl_embed/drop-dead-code` | dead helpers/constants, `main` smoke + skip, fixture `tests/test_rtl_embed.py:89` | zero |
| rt3 | `rtl_embed/drop-fallbacks` | read schema-4 fields directly | zero |
| rt4 | `rtl_embed/drop-dead-fields` | 12 unread record fields | minus keys |
| rt5 | `rtl/relative-paths` | `rtl_ingest` writes paths POSIX-relative to `RTL_DIR` | `file` values |
| rt6 | `rtl_embed/instances-only` | parents/children from instances, all parents | 3 module texts |
| rt7 | `rtl_embed/wiring-directions` | stated-direction `reads` / `drives` | `reads` lines |
| rt8 | `rtl_embed/drop-repeated-facets` | `structure`, `normalized`, construct heading + citation | shorter texts |
| rt9a | `rtl/preamble` | `rtl_ingest` module `preamble` | `rtl.json` plus key |
| rt9b | `rtl_embed/preamble-comments` | preamble + `/* */` first in `comments` | module texts |
| rt10 | `rtl_embed/windows` | `:wN` windows, drop caps, retrieve folds windows | coverage → 1.0 |
| rt11 | `rtl_embed/drop-terms-cap` | drop `_TERMS` | module texts |

## Outcome (2026-09-24)

All steps run; commits and MLflow runs in [`docs/NEXT_STEPS.md`](../NEXT_STEPS.md).

| | rt0 | rt11 |
|---|---|---|
| records (module / construct, windows included) | 35 / 119 | 82 / 241 |
| `rtl.embedded_line_coverage` | 0.53 | **1.0** |
| max bge tokens (module / construct) | 477 / 468 | 439 / 474 |
| `retrieval.rtl.exact_id_recall_at_k` | 0.400 | **0.458** |
| `retrieval.rtl.mrr_at_k` | 0.584 | 0.625 |
| RTL / spec section recall | 1.0 / 0.633 | 1.0 / 0.633 |
| `src/rtl_embed.py` lines | 848 | 379 |

Coverage is measured on non-blank block source lines (set match per module), so the rt0
value (0.53) differs from the line-extent estimate in the baseline (0.36).
