# Understanding ChipVer's Dual-RAG architecture

## What problem does the architecture solve?

Hardware questions come in two evidence domains. A specification says what an interface or
behavior means. RTL says what source structures, signals, and assignments exist. Searching
both as one undifferentiated corpus makes exact identifiers compete with prose and tempts a
small model to infer structure that parsers already know.

ChipVer therefore keeps two retrieval systems and one narrow identity bridge:

```text
question -> route prior -> Spec RAG -> RTL RAG -> bridge expansion -> lane scoring
credible evidence -> bounded context -> source-excerpt selection and validation
```

The design rule is:

> Parsing discovers structure. Retrieval finds evidence. The bridge aligns domains. The
> LLM reasons.

## Why are specification and RTL indexed separately?

They have different authoritative structure and different useful retrieval units.

The PDF outline supplies document, section, and subsection boundaries. Tables supply
structured rows. Specification retrieval therefore operates on section-bounded prose and
whole tables, then follows deterministic parent/sibling/child links for nearby context.

Verilator supplies module boundaries, declarations, elaborated instances, and executable
block locations. RTL retrieval therefore starts with modules, adds only useful structural
blocks, and keeps declarations as exact module-owned metadata.

Separate indexes keep ranking observable even though the current graph searches both for
every question. The route prior records what the user asked for; observed query type records
which lanes cleared their own relevance floors.

## What structure is retained from a PDF?

Only its hierarchy:

```text
Document
  Section
    Subsection
      Subsection
```

Every hierarchy record embeds its own parent, children, previous sibling, and next sibling
IDs. No model is involved. Retrieval can expand a hit by following those links without
maintaining another service.

Tables remain first-class evidence but are not hierarchy records. Each table chunk stores
its caption, raw columns and rows, page, owning section/subsection, and Markdown retrieval
surface. Structured cells are persisted before rendering.

## How are specification signals discovered?

Ports and register fields come from structured table columns. Explicit `Signal` and
`Port` headers qualify directly. A generic `Name` header needs width, direction,
address, instruction, or signal-caption evidence. Dotted uppercase instruction mnemonics
may also come from prose because some PDF tables flatten into running text. Conservative
identifier rules prevent arbitrary headings and prose mentions from becoming entities.

Each signal retains its original spelling, normalized spelling/tokens, optional width,
direction and description, and complete source provenance. Re-running extraction produces
the same stable IDs and ordered JSON.

## What does Verilator contribute?

It is the authority for RTL syntax and structure. The compiler-derived representation
stores, per module:

- original and normalized module name;
- source file, start/end line, source code, and hash;
- ports, signals, parameters, types, widths, and ranges;
- reader and writer sites;
- elaborated instances and formal/actual bindings;
- always blocks, functions/tasks, assignment groups, and useful remaining blocks.

This data exists for retrieval and navigation. Module and block records are vectorized;
signal lookup stays exact and returns the owning module plus declaration and usage evidence.

## Why preserve both original and normalized names?

Identifiers are evidence and must never be rewritten. Normalization exists only to improve
retrieval and alignment. It splits punctuation and case boundaries, lowercases retrieval
tokens, and expands configured whole-token abbreviations. Thus `fpu_top_wrapper` can carry
the retrieval tokens `floating point unit top wrapper` while its original identifier
remains unchanged in every citation.

## What exactly does the bridge claim?

Only this:

> These specification and RTL records probably refer to the same hardware concept.

It does not claim that the RTL satisfies, implements, or contradicts specification
behavior. An alignment tells the comparison route where to look next.

For each specification signal, the bridge keeps several cheap name candidates. It then
scores available width, direction, section/module, description/usage, and table context.
Full-build semantic reranking sees only structural survivors. Optional local-model
resolution receives one bounded shortlist at a time, is capped across the build, and may
abstain. Its confidence can only lower the deterministic score.

Accepted links have numerical confidence and `WEAK`, `MEDIUM`, or `STRONG` labels.
Low-scoring or ambiguous cases are stored as `UNRESOLVED` with their candidate evidence.
False links are deliberately more expensive than missing links.

It is valid for every row in a bridge build to remain unresolved. Comparison still searches
both domains from the original question. A high-scoring candidate shortlist belonging to a
selected unresolved row can narrow inspection without becoming an accepted alignment.

## How are sections aligned to modules?

The bridge compares normalized section titles with module names, contextual tokens with
module/signals, and the distribution of already accepted child-signal mappings. It retains
several candidates and rejects close or sub-threshold choices. This provides a second way
to target RTL retrieval without promoting a signal coincidence into behavioral proof.

## How does retrieval rank evidence?

Specification retrieval uses four signals in order:

1. token-bounded exact identifier hits;
2. BM25 lexical candidates;
3. dense candidates from the specification FAISS index;
4. cross-encoder reranking of the fused bounded shortlist.

Exact hits remain ahead of semantic candidates. Hierarchy expansion happens after ranking
and adds only bounded neighboring evidence.

RTL retrieval first checks module/function names and module-owned declaration metadata.
Writer/reader blocks for an exact signal follow. BM25 and dense module/block retrieval plus
cross-encoder reranking fill remaining slots. Bridge targets can independently select a
bounded set of surrounding compiler blocks.

## How does a question move through the graph?

A deterministic classifier records one of `SPEC`, `RTL`, or `COMPARISON` as requested
intent. LangGraph then runs this fixed path for every question:

```text
classify -> retrieve spec -> retrieve RTL -> bridge lookup -> evidence expansion
         -> lane allocation -> optional bounded recovery -> merge -> answer
```

Searching both lanes prevents a route mistake from making evidence unreachable. The route
prior still controls which domain citations the answer requires. A comparison needs both
lanes to clear their floors; a single-domain request needs its requested lane. Otherwise the
model is skipped and the result is insufficient evidence.

## What does the local model receive?

One final evidence package containing:

- the question and route;
- cited specification chunks and relevant structured signals;
- signal and section/module alignment rows selected during bridge lookup;
- cited RTL module/block evidence and read/write context.

The default cap is 12,500 characters inside a 4,096-token model window, with separate
domain caps and explicit truncation. The model is instructed to return JSON containing only
known citation labels and verbatim source excerpts. Comparisons require citations from both
domains and are labeled unverified.

It does not search, parse, traverse, align, simulate, prove conformance, or issue behavioral
verdicts.

## How can a developer explain one answer?

`agent.py` prints a structured JSON trace to standard error. A comparison exposes:

```text
QUERY TYPE: COMPARISON
SPEC RETRIEVAL: chunk IDs, count, selected signal names
BRIDGE LOOKUP: mapping counts, signal targets, confidence
RTL RETRIEVAL: modules, record IDs, exact block line ranges
EVIDENCE MERGE: final character count
ANSWER: whether the model was called
```

The trace records evidence flow, not hidden model reasoning or the evidence body. It answers
the useful debugging questions: Which route ran? Which stable records were retrieved? Which
signal alignment narrowed the search? Which source blocks reached the model? How large was
the merged context? Did an empty evidence lane prevent the model call?

## How does rebuilding avoid drift and duplication?

Stable source-derived IDs make each record replaceable. Content hashes make changes
visible. Specification and RTL embedding manifests map each indexed ID to the exact text
fingerprint; unchanged indexes skip encoder work, and query-time adapters reject stale
indexes. Bridge inputs are byte-fingerprinted, output is sorted, and publication is atomic.

A full rebuild is intentionally simple:

```bash
python src/build.py --clean
```

All derived data lives below `output/`, so this command establishes a known clean boundary
without touching source PDFs or RTL.

## How is the architecture tested?

Hermetic tests pin parsing schemas, required provenance, stable IDs, exact-first retrieval,
manifest construction/status, bridge thresholds and ambiguity, all three routes, mandatory
both-domain comparison retrieval, evidence budgets, traces, and rebuild ordering:

```bash
python -m pytest -q -p no:cacheprovider tests
```

The current-schema end-to-end benchmark exercises built artifacts and reports
specification/RTL Recall@K and MRR, bridge
Recall@K/MRR/Top-1, routing accuracy, comparison both-domain rate, citation completeness,
answer outcomes, and latency:

```bash
python src/bench.py
```

An architecture regression is visible as a contract failure or a retrieval, routing, or
alignment metric change instead of only as a subjective answer difference.

## Which functions own each step?

This inventory maps every production module to its internal methods. Leading underscores
mean a helper is private to its module, not that its decision is unimportant.

### Configuration, names, roles, and build

- configure.py groups every path, model, parser rule, retrieval budget, bridge threshold,
  evaluation path, and context limit. Importing it installs the project's abbreviation map.
- naming.py: _raw_tokens splits names; set_abbreviation_aliases installs vocabulary;
  normalize_identifier makes identity text; identifier_tokens makes search words;
  normalize_module_name makes expanded module text.
- roles.py: _compile builds patterns; _normalize makes score shares; leading_role applies
  the ambiguity margin; chunk_role_scores and document_role_scores score source wording.
- build.py: _validated_output_dir protects deletion; remove_obsolete_artifacts removes
  retired output; clean_generated_data recreates output; stage_scripts and stage_command
  define execution; run_pipeline runs it; main parses options.

### Specification side

- ingest.py: slugify, find_revision, parse_section_number, and _subtree_end_page establish
  identity and bounds. build_sections creates inline hierarchy. repeated_lines,
  _clean_pdf_text, and section_page_texts recover prose. is_caption, build_caption_index,
  table_region, rows_by_alignment, unstack_row, extract_table, rows_to_markdown,
  build_tables, and find_table_refs recover tables. enrich_spec_record adds the common
  contract. split_sentences, SentenceWindowSplitter.split_units,
  SentenceWindowSplitter.split_text, and sliding_window_chunks create passages.
  ingest_pdf, hierarchy_document, and main assemble and publish artifacts.
- spec_signals.py: _header_key, _column_index, _cell, parse_width, parse_direction,
  parse_source_role, prose_widths, _parsed_cell, and _signal_name interpret source cells.
  _signal_record, signals_from_table, instructions_from_prose, and build_spec_signals create
  entities. save_spec_signals and main publish them.
- embed.py: available_device, content_hash, chunk_fingerprint, record_fingerprint,
  load_chunks, build_manifest, and record_to_document prepare records.
  Embedder.embed_documents and Embedder.embed_query encode them. VectorStore.build, save,
  load, and search own FAISS. read_manifest, write_manifest, and main own freshness.
- retrieve.py: tokenize, identifier_terms, and exact_document_ids provide literal search.
  BM25Index provides lexical search. reciprocal_rank_fusion merges ranks.
  document_authority and apply_authority add the source-role tie-break. Reranker scores
  pairs. Retriever.load, candidates, and search assemble the hybrid.

### RTL side

- rtl_ingest.py: parse_loc, file_table, source_name, read_lines, and block_extent recover
  source. _parent_map, _owned_by, _scope_names, _generate_node, and _generate_id recover
  ownership. _symbol_kind, _symbol_base_id, _integer, _range_descriptor, and
  type_descriptors interpret declarations. _enum_item, enum_types, and enum_usage handle
  enums. _declaration_record, _assign_symbol_ids, symbol_records, and _symbol_lookup build
  symbols. ref_names, _assignment_signal_roles, _construct_signal_roles, _sensitivity,
  statement_site, collapse_sites, and driver_reader_sites derive use. _cell_paths,
  _instance_record, _module_extents, _block_type, _block_record, _assignment_groups,
  modules, and tree build structure. parse_warnings, run_verilator, build, and main publish
  the compiler view.
- rtl_embed.py: _source_id, _record, comments, children, _signal_metadata, _terms, and
  port_connections create retrieval context. chunks, _structural_records, and
  retrieval_records create surfaces. load_records and index_manifest_status handle
  artifacts. _query_terms, _term_strength, _phrase_strength, and exact_lookup handle names.
  RTLRetriever._hybrid and RTLRetriever.search add semantic fill; search and main expose the
  query and build entrypoints.

### Bridge, agent, and answer

- bridge.py canonicalizes schemas with _as_list, _first, _unique, spec/RTL canonicalizers,
  and enum/module flatteners. Name helpers and generate_signal_candidates create
  candidates. infer_module_roles, expected_direction, _component, _weighted_components,
  and structural_signal_score add deterministic evidence. Pair-text helpers,
  _score_adapter, _semantic_unit, and rerank_signal_candidates add optional semantic
  scores. ambiguity_evidence, _resolver_result, _collapse_threaded_ports,
  confidence_level, and align_signal enforce safe selection. align_enum,
  _module_candidate, and derive_module_alignments cover other identities. Fingerprint,
  build, lookup, load, atomic-write, resolver, argument, and main functions complete the
  bridge lifecycle.
- agent.py uses classify_query, lane scoring/calibration, and observed_route for intent and
  evidence quality. Hit, deduplication, hierarchy, entity, bridge, staging, and block
  helpers assemble evidence. Context renderers, merge_evidence, and citable_evidence make
  safe model input. Recovery helpers bound re-querying. Runtime lazily owns artifacts and
  models. build_graph defines nine state-transition nodes; run, ask, trace_report, and main
  expose execution.
- answer_contract.py: validate_answer enforces exact schema, known citations, verbatim
  excerpts, and required domains; render_answer emits validated evidence or fixed
  abstention text.

### Evaluation

- bench.py owns keyword diagnostics, Recall@K, MRR, routing, alignment, citation, latency,
  state capture, per-lane retrieval rows, miss reports, recovery comparison, and JSON
  report entrypoints.
- release_eval.py: digest binds data; validate_dataset rejects leakage and vacuous cases;
  run_cases records real output; release_gate combines human review and hard thresholds;
  main generates or rescores the report.

## How should a surprising answer be traced?

1. Check route_prior for requested intent.
2. Inspect spec and RTL retrieval IDs.
3. Check selected entities and bridge candidates.
4. Inspect targeted RTL file and line ranges.
5. Compare lane scores with floors and credibility.
6. Read final_context, the exact model input.
7. Compare citable_evidence with returned citations and quotes.
8. Read answer_validation errors when output was rejected.

That order separates parsing failure, stale data, retrieval miss, alignment error, context
truncation, weak evidence, model selection error, and citation rejection.

## What can the final answer establish?

answer_contract.py establishes exact quote provenance: a known label and a verbatim passage.
It does not establish relevance, complete coverage, semantic entailment, simulation results,
formal proof, equivalence, or corpus-wide absence.
