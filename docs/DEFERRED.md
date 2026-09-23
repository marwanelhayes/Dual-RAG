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

## DF-04: Signal direction in `spec_signals`

Decided 2026-09-23. Current choice: `spec_signals` records no direction. No table in the
AXI or RISC-V corpus has a Direction, Dir, or I/O column; the field was null on all 1100
signals at `ingest/caption-regex`, and the `Source` column holds interface roles
(`Manager`, `Tx`), not directions.

Deferred option: one `direction` field holding the raw cell, read from a column headed
`direction`, `dir`, `signal direction`, `port direction`, or `i/o`. No normalization map.

Why not now: no corpus document exercises it, so it cannot be tested on real data.

Reopen when: a spec with a direction column is ingested (Xilinx/AMD `I/O`, Intel
`Direction`), typically at the DF-03 holdout.

## DF-05: Instruction mnemonics in `spec_signals`

Decided 2026-09-23. Current choice: `spec_signals` extracts ports and CSRs only. Instruction
mnemonics (`FADD.S`, `RNE`) came from mnemonic tables and a regex over all prose: 441 of 1088
rows at `v0-baseline`.

Deferred option: an instruction inventory, as its own artifact, not as signals.

Why not now: a mnemonic is not a signal or a module and never appears as an RTL identifier;
justifying it needs a later stage (agent ISA questions), which remove-first forbids.

Reopen when: a walked consumer shows a measured loss (retrieval or agent eval) from its
absence.

## DF-06: Register field layouts in `spec_signals`

Decided 2026-09-23. Current choice: whole CSRs come from address tables; sub-register fields
are not extracted. Dropped: `Bits | Name | Description` tables (riscv-unprivileged 23, 44,
48, 49: `CBIE`, `vma`, `vta`, `vill`) and suffixed names (`vsew[2:0]`, `vlmul[2:0]`,
`vxrm[1:0]`).

Deferred option: a register-field record with the parent register and the bit position read
from the `Bits` cell (never as a width), and `name[hi:lo]` split into name + range.

Why not now: fields are slices of a register, the same category as rejected bit slices
(`ACADDR[51]`); only field-level linking would need them.

Reopen when: RTL linking needs field-level matches (CSR field decode in RTL).

## DF-07: `row_kind` (port vs register) in `spec_signals`

Decided 2026-09-23. Current choice: no port/register classification; provenance
(`table_label`, `table_caption`) says where a row came from. The heuristic misfiled
`BROADCASTATOMIC` (a tie-off input) and the DVM message fields as registers.

Deferred option: classify from table evidence (Source column → port; address column →
register).

Why not now: extraction does not need the split; only a consumer would. `bridge.py` reads
`row_kind` today, so the field is removed at the bridge walk (pending consolidation).

Reopen when: the bridge walk shows linking needs port vs register.

## DF-08: Metadata filters in Chroma

Decided 2026-09-23. Current choice: Chroma entries hold `id`, embedding, and embedded text
only; hits are hydrated from `chunks.jsonl` / `rtl_chunks.jsonl` by id.

Deferred option: store filterable metadata (`document_id`, `chunk_type`, `section_path`) in
Chroma and query with `where=`. Four fields cannot be stored as-is (`document_role_scores`,
`rows`, `sensitivity`, `signal_metadata`: dicts or nested lists) and would need encoding.

Why not now: no caller filters; storing metadata duplicates the jsonl truth.

Reopen when: a retrieval path needs to restrict search by document, type, or section.

## DF-09: Multi-vector embedding of over-limit chunks

Decided 2026-09-23. Current choice: `embed.py` encodes each chunk once; text past 512 bge
tokens is truncated (52 chunks, 12,068 tokens at step 9b, mostly Sail code / hex tables).

Deferred option: split an over-limit chunk into several vectors that share its `chunk_id`.

Why not now: chunk size is ingest's job (steps 13 code-line split, 14 token caps); ids stay
1:1 with chunks.

Reopen when: ingest step 14 lands and `ingest.chunks_over_limit` is still above 0.
