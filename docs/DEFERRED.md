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

## DF-10: Elaborated (parameter-specialised) module variants

Decided 2026-09-23. Current choice: `rtl.json` names modules by source name only; Verilator
clone names (`fetch_stage__A8_CDz1`) are dropped with `elaborated_modules`,
`target_elaborated_modules`, and the tree's `elaborated_module`.

Deferred option: keep one record per elaborated variant with its resolved parameters.

Why not now: the variants are compiler bookkeeping; no question distinguishes two
parameterisations of one module in this corpus.

Reopen when: a design instantiates one module with different parameters and questions ask
about a specific variant (widths per instance).

## DF-11: Clock and reset roles for blocks

Decided 2026-09-23. Current choice: blocks keep only `sensitivity` (signal + edge as
stated). The removed `reset` matched names against `(^|_)(rst|reset)(_|$)`, which misses
AXI `ARESETn`.

Deferred option: derive the clock as the edge signal not tested in the block, and the reset
as the edge signal tested by the block's first `if`, from the AST.

Why not now: no consumer reads the roles.

Reopen when: a query needs "which clock / reset drives X".

## DF-12: Initial blocks and stray `other_blocks`

Decided 2026-09-24. Current choice: `other_blocks` (3 `initial` memory/CSR inits, one a
`$readmemb` of an absolute path, and 2 constant `assign`s) are not RTL records.

Deferred option: embed them as construct records like the other blocks.

Why not now: simulation-only initialisation; no question asks about it.

Reopen when: a question asks how memory or CSR state is initialised.

## DF-13: Package and non-module file headers

Decided 2026-09-24. Current choice: `preamble` (rt9a) is taken for modules only; headers of
`shared_pkg.sv` and `csr_defs.sv` (28 and 25 comment lines) are not embedded.

Deferred option: one record per package with its header comment and enum/typedef names.

Why not now: packages are not modules in `rtl.json`; enums reach the bridge separately.

Reopen when: a question about a package-level definition (CSR addresses, shared enums)
misses in the RTL lane.

## DF-14: Tiny-model tie-breaker and bridge lookup helpers

Decided 2026-09-24. Current choice: a close tie between candidates stays unresolved.

Deferred option: ask a local model to choose between a bounded shortlist (`--llm`).

Why not now: the pipeline never ran it (`llm_fallback_calls: 0`).

Reopen when: ties become a main unresolved reason and a gold set shows they are real.

## DF-15: Port direction from spec Source role and module role votes

Decided 2026-09-24. Current choice: the bridge compares no direction; spec rows state none.

Deferred option: raw `Source` cell plus netlist driver to infer the expected port direction.

Why not now: switching it off changed 0 alignments; it needed a synonym map (`Tx` → manager).

Reopen when: a gold case shows direction separating two candidates.

## DF-16: Instruction to enum-member alignment

Decided 2026-09-24. Current choice: instruction rows leave spec_signals (s2), so the bridge
aligns none; `rtl_ingest` stops computing `enum_usage`.

Deferred option: align an instruction inventory (DF-05) to `fpu_operation_t` / ALU enums.

Reopen when: DF-05 reopens.

## DF-17: Contextual scoring (section, description, table caption)

Decided 2026-09-24. Current choice: links need a stated tier (identity, learned prefix, net).

Deferred option: weighted jaccards of section title, description and caption against module
name and usage text.

Why not now: about 0 on every AXI port; it hid 16 of 25 implemented signals.

Reopen when: two stated-tier candidates for one spec row need ranking.

## DF-18: Semantic cross-encoder rerank of bridge candidates

Decided 2026-09-24. Current choice: fuzzy scores only rank `SUGGESTED` candidates.

Deferred option: `--rerank` with the retrieval cross-encoder.

Reopen when: suggestions are used for links and their precision needs raising.

## DF-19: Fuzzy section-title to module matching

Decided 2026-09-24. Current choice: sections link to modules by title identity or link
support only.

Deferred option: title / context similarity scoring with thresholds.

Why not now: its above-0.45 candidates fed agent expansion with `PCIe` → `pc_adder`.

Reopen when: a gold section → module set exists to measure it.

## DF-20: Spec exact-identifier lane

Decided 2026-09-26. Current choice: spec `Retriever` = BM25 ∪ dense → cross-encoder.

Deferred option: `identifier_terms` / `exact_document_ids` literal lane pinning up to
`top_k // 2` chunks containing a question identifier.

Why not now: sets not selective (`MODE` 467 chunks, `RISC` 353, `RV32I` 116); BM25 already
ranks the same literal tokens; `exact_terms=` had no production caller.

Reopen when: identifier questions drop on `retrieve.spec.section_recall_at_k` after q3.

## DF-21: Frozen small-LLM document role classifier

Decided 2026-09-26. Current choice: `roles.document_role_scores` word counting feeds
`apply_authority` (normative outranks explanatory).

Deferred option: a "system 1" classifier with frozen weights (e.g. Qwen, the user's "open
jev style" idea) labelling each document normative or explanatory.

Reopen when: word counting mislabels a source, or a new corpus document lands.

## DF-22: Stated per-source role override

Decided 2026-09-26. Current choice: roles inferred only.

Deferred option: `cfg.SOURCE_ROLES = {doc_name: role}` overriding inference.

Why not now: users are not expected to state document types.

Reopen when: a user states a document's type.

## DF-23: Casefold and normalized exact-lookup tiers

Decided 2026-09-26. Current choice: RTL exact lookup matches names case-exactly, plus
phrase matches on `normalized_name`.

Deferred option: casefold and `normalize_identifier` tiers in `_term_strength`.

Why not now: bench: normalized fired 0 times; casefold fired 7, all wrong (English
"rounding" → function `Rounding`, "MODE" → signal `mode`), pinned ahead of gold.

Reopen when: users type identifiers in a different case or separator style than the RTL.

## DF-24: Query stop words and minimum term length

Decided 2026-09-26. Current choice: every identifier-shaped query word is a lookup term;
`signal_terms` still keeps all-lowercase words away from signal names.

Deferred option: `_QUERY_STOP_WORDS` and `len >= 3` filter in `_query_terms`.

Why not now: no stop word is an RTL name (15 are SystemVerilog reserved words); the length
filter hid `PC` / `WE`.

Reopen when: a capitalised article or short word pins a wrong signal (watch
`SPEC_PRIV_MCAUSE`: `M` → `non_restoring_divider`).

## DF-25: Normalized names in `signal_names`

Decided 2026-09-26 (after q7). Current choice: `signal_names` holds declaration names as the
source spells them.

Deferred option: also store each declaration's `normalized_name`, so a query spelling
`csr_file` exact-hits signal `CsrFile`.

Why not now: the only bench effect is `CMP_MINFO_CSR_EXACT` (-1 gold construct), where the
question already names module `csr_file`; the gain came from a module-name / signal-name
coincidence.

Reopen when: questions spell signals in snake_case form and miss their mixed-case RTL names.
