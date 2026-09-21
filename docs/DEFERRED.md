# Deferred decisions

Options considered and intentionally not built yet. Each entry states why it was deferred
and the measurable condition that reopens it.

## DF-01: Late chunking for section-aware embeddings

Decided 2026-09-21. Current choice: each chunk, including each piece of a split oversized
sentence, is embedded with its section `context_header` plus sentence overlap. Neighbouring
chunks are reachable through `hierarchy_id` + `chunk_index`.

Deferred option: embed a whole section in one encoder pass, then mean-pool the token vectors
falling inside each chunk's span, so every chunk vector is conditioned on its full section.

Why not now:

- `BAAI/bge-small-en-v1.5` has a 512-token window; most sections exceed it, so late chunking
  needs a long-context embedder (8k-class). That is a model swap plus a new dependency.
- The pipeline is in a remove-first simplification pass; this is the largest addition on
  the table and would confound every other ingest change being measured.
- No baseline yet shows section-awareness is the bottleneck.

Reopen when:

- the `MAX_CHUNK_TOKENS` sweep shows section-level recall plateauing while
  `fact_coverage_at_k` still misses facts that sit in a neighbouring chunk of the right
  section; or
- a long-context embedder is adopted for another reason.

Evaluate as its own MLflow run (tag `experiment=late_chunking`) against the best sweep run,
comparing section recall, `fact_coverage_at_k`, and embed time.

## DF-02: Column-splitting of wide table rows

Decided 2026-09-21. Current choice: table rows are atomic. A chunk holds whole rows with the
repeated column header; a single row over `MAX_TABLE_CHUNK_TOKENS` is admitted whole. The
512-token hard limit (header + body) fails the run if a row ever exceeds the encoder window.

Deferred option: when a row has so many columns that it breaks the token bound, split it
by columns into several chunks, repeating the key column (e.g. the signal name) and the
matching header cells in every piece.

Why not now (measured on the 4-PDF corpus, 2,336 unique rows, 2-11 columns, bge tokens):

- at a 128-token cap only 6 rows exceed it, and only 1 of those because of column count;
  the other 5 overflow from one long description cell, which column-splitting cannot fix;
- at a 256-token cap no row exceeds it; the largest row is 251 tokens, far below 512.

Reopen when:

- any table row exceeds the 512-token hard limit; or
- `ingest.table_rows_over_cap` shows a non-trivial number of rows over cap whose overflow
  comes from column count (e.g. a new PDF with wide register maps).

## DF-03: Cross-vendor holdout for ingest generality

Decided 2026-09-21. Current target: text-layer PDFs of professionally typeset hardware
specifications, validated on Arm (AXI) and RISC-V only.

Deferred option: ingest one spec from a third vendor (IEEE, Intel, JEDEC, ...) as a holdout
and log its intrinsic ingest metrics as a separate MLflow run.

Why not now: no third-vendor spec is available yet.

Known corpus-tuned pieces the holdout would test:

- caption regex (`Table 44.`, `Table A2.2:`), which misses `TABLE I`, `Table 3-1`, and
  captions set below their table;
- the continuation banner "Continued from previous page", which misses "(continued)";
- `find_revision` dialects ("Issue", "Version"), which return "unknown" otherwise;
- position furniture bands, which assume one page size per document.

Reopen when: a spec from another vendor is added to `Ingest/`. Fix only the pieces whose
holdout metrics (heading anchor rate, tables found, unresolved table refs, furniture
stripped) show breakage.
