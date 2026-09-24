# Spec: `src/ingest.py`

Decided 2026-09-21 in the simplification pass. Deferred options live in
[`docs/DEFERRED.md`](../DEFERRED.md) (DF-01 late chunking, DF-02 column splitting, DF-03
cross-vendor holdout).

## Target

Text-layer PDFs of professionally typeset hardware specifications. Validated on Arm
(`AXI spec.pdf`, `AXI waveform.pdf`) and RISC-V (`riscv-privileged.pdf`,
`riscv-unprivileged.pdf`). Structure in these documents is meaningful and is preserved.

Non-goals: scanned PDFs, figures and waveforms (need vision), a separate `code` chunk type,
per-chunk semantic roles.

## What ingest must do

1. **Read the PDF** and its revision (`find_revision`). The revision is metadata only; it is
   not part of embedded text.
2. **Hierarchy from the outline.** Use the embedded outline with destination points
   (`get_toc(simple=False)`): each node records `(page, y)` of its heading. When a PDF has no
   outline, fall back to typography and numbering (`detect_headings`); the fallback is kept
   and validated against a real outline (metric `ingest.fallback_heading_f1`).
3. **Strip page furniture by position.** Blocks in the outermost top and bottom y-bands that
   recur on at least 60% of pages are headers and footers, whatever their text. This catches
   running headers whose text changes per page (AXI y=69, RISC-V y=12).
4. **Section text by position.** A section's text is every body block from its heading's
   `(page, y)` to the next outline entry's `(page, y)`. No title string search. The first
   block at the start point is dropped when it equals the outline title (the breadcrumb
   already names the section). Sections are
   skipped only when empty or titled contents / index / bibliography.
5. **Tables.** A caption is the first line of a block matching
   `Table <label><sep> <title>` where the separator is `.` or `:` followed by whitespace.
   Rows are extracted by ruling lines, then the page-break carry, then column alignment.
   Tables continued under a "Table X – Continued from previous page" banner append the
   continuation page's rows (repeated header row dropped). A table is owned by the section
   whose `(page, y)` span contains its caption.
6. **Prose excludes tables.** Blocks inside a detected table region, including continuation
   regions, never enter prose. Tables are linked to prose at section level through
   `hierarchy_id`; cross-section mentions ("see Table A2.2") resolve into `table_ids`.
7. **Sentence chunking.** Split on sentence punctuation (stdlib `re`, abbreviations
   rejoined). Pack sentences into windows up to `MAX_CHUNK_TOKENS` (bge tokenizer), with
   `OVERLAP_SENTENCES` trailing sentences reopening the next window. A unit over the cap
   with no sentence punctuation (code listings) is split at its line breaks, so newlines
   survive until splitting.
8. **Table chunking.** Rows are atomic. Windows of whole rows up to
   `MAX_TABLE_CHUNK_TOKENS`, every window restating the column header. A single row over the
   cap is admitted whole.
9. **Section-aware embedding text.** `context_header` = `doc_name > section path` for prose
   and `doc_name > section path > Table X: caption` for tables.
10. **Hard limit.** `context_header` + body must be at most 512 tokens for every chunk; a
    violation fails the run rather than being silently truncated by the encoder.
11. **Document role.** Score each document's normative/explanatory role once and stamp
    `document_role_scores` on its chunks (consumed by retrieval authority).

## Chunk contract

Common fields: `chunk_id`, `chunk_type`, `record_id`, `source_id`, `document_id`,
`doc_name`, `source_file`, `revision`, `hierarchy_id`, `section_id`, `section_title`,
`subsection_id`, `subsection_title`, `section_number`, `section_name`, `section_path`,
`parent_id`, `context_header`, `start_page`, `end_page`, `page`, `table_ids`,
`content_hash`, `document_role_scores`, `text`.

Prose adds: `chunk_index`.

Table adds: `table_label`, `table_id`, `caption`, `columns`, `rows`, `n_rows`, `row_start`,
`table_rows`, `extraction`.

Removed (no consumer): `chunk_role`, `chunk_role_scores`, `chunk_role_source`,
`document_role`, `sentence_sources`, `prev_section`, `next_section`, `parent_name`,
`n_sentences`, `tables_cited`, `parent_number`, `level`, `own_start_page`, `own_end_page`,
`section_start_page`, `section_end_page`, `n_chars`.

Pending consolidation (decided, done when `spec_signals`, `agent`, `bridge` are walked):
consumers switch to `hierarchy_id` + `section_path`; then `section_id`, `section_title`,
`subsection_id`, `subsection_title` are removed.

## Metrics (`track.py`, prefix `ingest.`)

| Metric | Expectation |
|---|---|
| `sections`, `tables_found`, `chunks` (per document) | tracked |
| `heading_anchor_rate` | headings found at their outline destination; near 1.0 |
| `fallback_heading_f1` | `detect_headings` vs real outline on riscv-privileged |
| `table_rows_total` | rises when continuation banners are followed |
| `tables_with_zero_rows` | falls (A7.3, A7.4 leave it) |
| `table_refs_unresolved` | prose refs to a table label with no table chunk; 65 at `v0-baseline` |
| `table_owner_changed` | tables whose owner differs from baseline |
| `prose_table_overlap` | prose chunks containing a table header row; 0 |
| `chunk_tokens_p50/p95/max` | tracked per chunk type |
| `chunks_over_limit` | must be 0 |
| `oversize_units_split` | code listings split at line breaks |
| `table_rows_over_cap` | rows admitted whole over the table cap |
| `furniture_blocks_stripped` | per document |
| `furniture_text_only_hits` | lines only text matching strips; 0 justifies its removal |
| `header_leaks` | chunks matching `\| Page \d+` or `Chapter A\d+\.`; 0 |
| `chunks_bytes` | size of `chunks.jsonl` |

Retrieval guardrail (`bench.py --retrieval`): the decision metric is
`section_recall_at_k` (step 0b). Each gold spec chunk carries a
`relevant_spec_sections` label `{chunk_id, section, page}` where `section` is
`bench.section_key`: `doc::section_number` (or section name) for prose,
`doc::table::label` for tables. Retrieved chunks are mapped through the current
`chunks.jsonl`, so re-chunking and renumbering do not break it; page is informational.
Every other retrieval metric is scored on chunk IDs and is logged but never used for
decisions. `family_recall_at_k` is also inflated: an unwindowed table ID
(`doc#table#25`) loses its label to the suffix strip, so any table in the document
matches (seen on `SPEC_RV_ROUND_MODES`: 0.615 section vs 0.623 family at step 1).

## Execution queue

Every step: failing test first, full suite green, commit, `build.py --clean`,
`bench.py --retrieval`, `track.py --tag <tag>`.

| Step | Tag | Change | Expected diff |
|---|---|---|---|
| 0 | `v0-baseline` | `track.py` + local SQLite MLflow; commit tree without the caption fix | baseline |
| 0b | `eval/gold-sections` | add section + page labels to gold from baseline `chunks.jsonl`; section recall in bench | metrics only |
| 1 | `ingest/caption-regex` | caption regex fix, `is_caption` deleted (already applied, held out) | +16 RISC-V tables |
| 2 | `ingest/drop-langchain-splitters` | stdlib `re.split`; call `sliding_window_chunks` directly; drop dependency | zero chunk diff |
| 3 | `ingest/drop-dead-fields` | remove the 6 + 7 unconsumed fields | zero text diff |
| 4 | `ingest/remove-chunk-roles` | drop chunk roles from ingest, `roles.py`, config, tests | zero text diff |
| 5 | `ingest/drop-tables-cited` | keep `find_table_refs` → `table_ids`; drop `tables_cited` | zero text diff |
| 6 | `ingest/header-no-revision` | revision out of `context_header` | vectors shift |
| 7 | `ingest/furniture-bands` | add position bands; log `furniture_text_only_hits` | headers leave text |
| 7b | `ingest/drop-text-furniture` | delete `repeated_lines` if the metric is 0 | zero text diff |
| 8 | `ingest/fallback-validation` | log `fallback_heading_f1` | metrics only |
| 9 | `ingest/position-sections` | `(page, y)` boundaries; drop `MIN_SECTION_CHARS`; keep newlines | chunk IDs shift |
| 9b | `ingest/drop-heading-block` | first block on the start page dropped when it equals the outline title | headings leave text |
| 10 | `ingest/prose-excludes-tables` | table regions out of prose | overlap to 0 |
| 11 | `ingest/table-owner-position` | owner by `(page, y)`; section path in table header | owners move |
| 12 | `ingest/table-continuations` | follow continuation banners | rows rise |
| 12b | `ingest/wrapped-cells` | a wrapped description line joins its row instead of becoming a new row that repeats the name (riscv-unprivileged Table 23: `CBIE` ×7) | rows fall |
| 13 | `ingest/code-line-split` | oversize units split at line breaks | no chunk over cap |
| 14 | `ingest/token-caps` | `MAX_CHUNK_TOKENS`, `MAX_TABLE_CHUNK_TOKENS`, 512 hard limit, env override | sizes change |
| 15 | `sweep/chunk_tokens` | grid 128 / 256 / 384 prose; 1-row / 128 / 256 tables | pick by metrics |

## Outcome of steps 10-15 (2026-09-24)

Section recall 0.633 (9b) → **0.675** at the swept caps (384 prose / 128 table tokens,
`MAX_CHUNK_TOKENS` / `MAX_TABLE_CHUNK_TOKENS` env overrides). `prose_table_overlap` 404 → 49
(the rest: uncaptioned tables sharing a header shape). Table rows 2471 → 2964. Chunks over
512 tokens 52 → 0; the run fails on any. Not added: `oversize_units_split`,
`table_owner_changed` metrics (diffs were computed per step instead).
