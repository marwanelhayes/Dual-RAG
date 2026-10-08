# Understanding ChipVer's Dual-RAG architecture

## What problem does the architecture solve?

Hardware questions come in two evidence domains. A specification says what an interface or
behavior means. RTL says what source structures, signals, and assignments exist. Searching
both as one undifferentiated corpus makes exact identifiers compete with prose and tempts a
small model to infer structure that parsers already know.

ChipVer therefore keeps two retrieval systems and one narrow identity bridge:

```text
question -> route prior -> Spec RAG -> RTL RAG -> bridge lookup -> whole-passage context
         -> source-excerpt selection and validation
```

The design rule is:

> Parsing discovers structure. Retrieval finds evidence. The bridge aligns domains. The
> LLM reasons.

## Why are specification and RTL retrieved separately?

They have different authoritative structure and different useful retrieval units.

The PDF outline supplies document, section, and subsection boundaries. Tables supply
structured rows. Specification retrieval therefore operates on section-bounded prose windows
and table row windows, each carrying its section breadcrumb.

Verilator supplies module boundaries, declarations, elaborated instances, and executable
block locations. RTL retrieval therefore starts with exact names, adds the structural blocks
that read or write a named signal, and ranks module and block records lexically.

Separate lanes keep ranking observable even though the graph searches both for every
question. The route prior records what the user asked for and decides which domains the
answer must cite.

## What structure is retained from a PDF?

Its hierarchy:

```text
Document
  Section
    Subsection
      Subsection
```

Every hierarchy record stores its parent, children, and siblings. No model is involved.
The bridge uses section records to link sections to modules; chunks cite their section.

Tables remain first-class evidence but are not hierarchy records. Each table chunk stores
its caption, raw columns and rows, page, owning section, and Markdown retrieval surface.
Structured cells are persisted before rendering.

## How are specification signals discovered?

Only from table chunks; prose is never read. A table qualifies by its headers: an explicit
`Signal` or `Port` column, or a `Name` column plus a width, source, or address column. The
name cell must be a plain identifier. One record is one table row, with its original
spelling, normalized tokens, width cell, description, and full provenance.

## What does Verilator contribute?

It is the authority for RTL syntax and structure, and the only parser. The design's
package files and top module are found from the sources (the top is the module no other
source instantiates). The compiler-derived representation stores, per module:

- original and normalized module name;
- source file, start/end line, and source;
- ports, signals, parameters, types, widths, and ranges;
- reader and writer sites;
- elaborated instances and formal/actual bindings;
- always blocks, functions/tasks, and assignment groups.

Module and block records are ranked by exact lookup, BM25, and the reranker; they are not
embedded. Signal lookup stays exact and returns the owning module and the blocks that use it.

## Why preserve both original and normalized names?

Identifiers are evidence and must never be rewritten. Normalization exists only to improve
retrieval and alignment. It splits punctuation and case boundaries, lowercases retrieval
tokens, and expands configured whole-token abbreviations with a single hardware meaning
(`fpu`, `alu`, `csr`, `pc`, `rm`). Thus `fpu_top_wrapper` can carry the retrieval tokens
`floating point unit top wrapper` while its original identifier stays unchanged in every
citation. Ambiguous prefixes (`wb`: writeback or Wishbone, `rnd`: rounding or random) stay
literal.

## What exactly does the bridge claim?

Only this:

> These specification and RTL records name the same hardware identity.

It does not claim that the RTL satisfies, implements, or contradicts specification
behavior. An alignment tells the reader where to look next.

Each link states how it was found: `identity` (flat names equal, STRONG) or
`interface_prefix` (the RTL name is a prefix learned per module plus the spec name, MEDIUM). Widths are
reported as `equal`, `conflict`, `symbolic`, or `unstated`, never used to reject a link.
Unresolved rows keep up to three `SUGGESTED` candidates that never count as links. A bridge
build where every row stays unresolved is valid.

## How are sections aligned to modules?

Only on stated evidence: `title_identity` (normalized section title tokens equal the module
name's, STRONG) or `link_support` (the modules whose ports the section's own signal rows link
to, with counts, MEDIUM). Sections with neither are not written.

## How does retrieval rank evidence?

Specification retrieval:

1. BM25 candidates and dense candidates from the specification Chroma index;
2. one cross-encoder reranks their union, shifted slightly toward defining (normative)
   sources over explanatory ones.

RTL retrieval:

1. exact hits on compiler-stated module, function, task, and signal names;
2. the blocks that read or write a matched signal, alternating with the exact hits;
3. BM25 candidates reranked by the same cross-encoder fill the remaining slots.

The cross-encoder is loaded once per process and shared by both lanes.

## How does a question move through the graph?

A deterministic classifier records one of `SPEC`, `RTL`, or `COMPARISON` as requested
intent, using wording and identifiers that only the design declares (read from `rtl.json`,
minus the specification's own signal names). LangGraph then runs this fixed path for every
question:

```text
classify -> retrieve_spec -> retrieve_rtl -> lookup_bridge -> merge -> answer
```

Searching both lanes prevents a route mistake from making evidence unreachable. The route
prior controls which domain citations the answer requires: both for a comparison, else the
requested one. Without citable evidence in a required domain the model is skipped and the
result is insufficient evidence.

## What does the local model receive?

One final evidence package containing:

- the question and route;
- whole specification passages;
- signal and section/module alignment rows from the bridge;
- whole RTL module and block passages.

Passages are never truncated; the package fits the model's `AGENT_NUM_CTX` window. The model
is instructed to return JSON containing only known citation labels and verbatim source
excerpts. Comparisons require citations from both domains and are labeled unverified.

It does not search, parse, traverse, align, simulate, prove conformance, or issue behavioral
verdicts.

## How can a developer explain one answer?

`agent.py` prints a structured JSON trace to standard error. A comparison exposes:

```text
QUERY TYPE: COMPARISON
SPEC RETRIEVAL: chunk IDs, count, selected signal names
BRIDGE LOOKUP: mapping counts, signal targets, confidence
RTL RETRIEVAL: modules, record IDs, block line ranges
EVIDENCE MERGE: final character count
ANSWER: whether the model was called
```

The trace records evidence flow, not hidden model reasoning or the evidence body.

## How does rebuilding avoid drift and duplication?

Stable source-derived IDs make each record replaceable. Content hashes make changes
visible. The specification embedding manifest maps each indexed ID to its text fingerprint,
and the agent rejects a stale index. Bridge inputs are byte-fingerprinted, output is sorted,
and publication is atomic.

```bash
python src/build.py --clean
```

All derived data lives below `output/`, so this command establishes a known clean boundary
without touching source PDFs or RTL.

## How is the architecture tested?

Hermetic tests pin parsing schemas, provenance, stable IDs, top-module detection, routing,
retrieval contracts, bridge link tiers, mandatory both-lane retrieval, traces, and rebuild
ordering:

```bash
python -m pytest -q
```

Retrieval is scored on 60 questions with `python src/bench.py --retrieval`; decision metrics
are spec section recall and RTL exact recall. `python src/track.py --tag <step>` logs each
run to MLflow, one clean commit per run.

## Which functions own each step?

Public functions per module (helpers with a leading underscore are private to their module).

- `naming.py`: `set_abbreviation_aliases`, `normalize_identifier`, `identifier_tokens`,
  `normalize_module_name`.
- `roles.py`: `document_role_scores`.
- `build.py`: `remove_obsolete_artifacts`, `clean_generated_data`, `stage_scripts`,
  `stage_command`, `run_pipeline`, `main`.
- `ingest.py`: revision and hierarchy (`find_revision`, `parse_section_number`,
  `detect_headings`, `fallback_heading_scores`, `build_sections`); page furniture
  (`repeated_lines`, `furniture_bands`, `furniture_counts`, `page_text`, `is_heading_block`,
  `section_page_texts`); tables (`continuation_banners`, `build_caption_index`,
  `table_region`, `rows_by_alignment`, `unstack_row`, `nearest_ruled_table`, `extract_table`,
  `rows_to_markdown`, `table_row_windows`, `build_tables`, `find_table_refs`); chunks
  (`count_tokens`, `split_sentences`, `sliding_window_chunks`, `enrich_spec_record`,
  `content_hash`, `load_chunks`); publishing (`ingest_pdf`, `hierarchy_document`, `main`).
- `spec_signals.py`: `signals_from_table`, `build_spec_signals`, `save_spec_signals`, `main`.
- `embed.py`: `available_device`, `record_to_document`, `Embedder`, `VectorStore`,
  `build_manifest`, `read_manifest`, `write_manifest`, `index_manifest_status`, `main`.
- `rtl_ingest.py`: sources (`design_sources`, `run_verilator`, `file_table`, `cited_path`,
  `read_lines`, `parse_loc`, `source_name`); declarations and use (`type_descriptors`,
  `enum_types`, `symbol_records`, `ref_names`, `statement_site`, `collapse_sites`,
  `driver_reader_sites`); structure (`block_extent`, `module_preamble`, `modules`, `tree`);
  publishing (`parse_warnings`, `build`, `main`).
- `rtl_embed.py`: `comments`, `port_connections`, `chunks`, `retrieval_records`, `main`.
- `retrieve.py`: `tokenize`, `BM25Index`, `document_authority`, `apply_authority`,
  `Reranker`, `shared_reranker`, `Retriever`, `exact_lookup`, `RTLRetriever`.
- `bridge.py`: `spec_signals`, `rtl_modules`, `rtl_signals`, `port_nets`,
  `interface_prefixes`, `enum_members`, `link_signals`, `derive_module_alignments`,
  `build_bridge`, `load_bridge`, `source_fingerprints`, `main`.
- `agent.py`: `classify_query`, `select_spec_signals`, `lookup_bridge`, `merge_evidence`,
  `citable_evidence`, `local_answer`, `Runtime`, `build_graph`, `run`, `trace_report`,
  `main`.
- `answer_contract.py`: `validate_answer`, `render_answer`.
- `bench.py`: answer scoring (`load_gold`, `score`, `tally`, `run`, `aggregate_metrics`) and
  retrieval scoring (`run_retrieval`, `stage_retrieval`, `retrieval_metrics`,
  `section_recall`, `lane_result`, `retrieval_main`, `main`).
- `track.py`: `pipeline_metrics` and its stage metrics, `main`.
- `release_eval.py`: `digest`, `validate_dataset`, `run_cases`, `release_gate`, `main`.

## How should a surprising answer be traced?

1. Check `route_prior` for requested intent.
2. Inspect spec and RTL retrieval IDs.
3. Check selected spec signals and bridge alignments.
4. Inspect the RTL file and line ranges that were retrieved.
5. Read `final_context`, the exact model input.
6. Compare `citable_evidence` with returned citations and quotes.
7. Read `answer_validation` errors when output was rejected.

That order separates parsing failure, stale data, retrieval miss, alignment error, model
selection error, and citation rejection.

## What can the final answer establish?

`answer_contract.py` establishes exact quote provenance: a known label and a verbatim
passage. It does not establish relevance, complete coverage, semantic entailment, simulation
results, formal proof, equivalence, or corpus-wide absence.
