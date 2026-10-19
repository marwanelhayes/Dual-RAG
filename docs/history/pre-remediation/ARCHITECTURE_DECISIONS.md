> Historical record archived on 2026-10-19. Commands, metrics, and descriptions below are not the current contract. See [current architecture](../../ARCHITECTURE.md).

# Current architecture decisions

This file records only decisions that constrain the current ChipVer implementation.
Historical alternatives are intentionally omitted. The system-level rule is:

> Parsing discovers structure. Retrieval finds evidence. The bridge aligns domains. The
> LLM reasons.

## AD-01: Two domain-local retrieval systems

Specification and RTL evidence use independent corpora, indexes, and retrieval adapters.
They meet only in the query agent and the small offline bridge.

Consequences:

- exact RTL identifiers do not compete with specification prose;
- domain-only questions run only one retriever;
- each corpus can choose its own useful retrieval unit;
- comparison behavior is testable as an explicit orchestration path.

## AD-02: The PDF outline is the complete specification hierarchy

Specification structure is derived deterministically from PDF outlines. A generated
document owns ordered section/subsection records with inline parent, child, previous, and
next links.

Tables are structured chunks and provenance, not hierarchy records. Text is never promoted
into additional relational structure.

## AD-03: Chunk within source boundaries and retain full provenance

Prose chunks never cross outline boundaries. Tables remain whole. Every specification chunk
retains stable ID, document/section/subsection, page, table IDs, source file, breadcrumbs,
and content hash. Table chunks additionally retain raw columns and rows before Markdown.

This makes retrieval results citable and permits bounded hierarchy expansion without
re-parsing a PDF.

> **Superseded on branch `simplify`:** Hierarchy expansion was removed (DF-27, [agent spec](specs/agent.md)); hierarchy links remain for citations and section-to-module alignment. Long tables are split into row windows that repeat the header row ([ingest spec](specs/ingest.md)).

## AD-04: Specification signals come only from structured tables

Signal extraction recognizes explicit signal/port columns and conservatively qualified
`Name` columns. It stores original and normalized names, tokens, optional width/direction/
description, and table/section/page provenance.

Prose mentions, headings, opcodes, and generic property rows are not signal sources. An
unextracted signal is preferred to a fabricated bridge input.

## AD-05: Verilator is authoritative for RTL structure

RTL ingestion compiles the configured SystemVerilog design through Verilator. The generated
artifact is module-centric and retains declarations, widths, instances/bindings, blocks,
source ranges/code, and compiler-derived reader/writer sites.

The local model never reconstructs syntax, hierarchy, or signal usage.

## AD-06: Vectorize modules and useful blocks, not declarations

Every RTL module is a retrieval record. Large modules are supplemented by always blocks,
functions/tasks, and assignment groups. Signals and instances remain module metadata.

Exact declaration lookup returns the owning module and matched signal evidence. Dense
retrieval fills the remaining slots with modules/blocks. This preserves navigability without
flooding the index with tiny rows.

> **Superseded on branch `simplify`:** RTL records are no longer vectorized: dense vectors lowered RTL recall, so the RTL lane ranks records by exact lookup, BM25, and the reranker (`retrieve/rtl-lexical-only`, [rtl_embed spec](specs/rtl_embed.md)).

## AD-07: Original identifiers are immutable evidence

Normalization adds retrieval forms; it never replaces source spelling. The shared naming
module performs deterministic case/punctuation splitting and configurable whole-token
abbreviation expansion. Both original and normalized module/signal names are persisted.

Stable source-facing identifiers and citations always use the original spelling.

## AD-08: Specification retrieval is exact-first hybrid retrieval

The specification retriever combines token-bounded exact matches, BM25, dense FAISS search,
reciprocal-rank fusion, and cross-encoder reranking of a bounded shortlist. Exact matches
stay ahead of semantic candidates.

After ranking, the agent may follow inline hierarchy links to add a bounded parent, sibling,
or child chunk. Expansion provides context; it does not alter the primary retrieval score.

> **Superseded on branch `simplify`:** The spec exact lane and reciprocal-rank fusion were removed; spec retrieval is BM25 ∪ dense Chroma candidates reranked by one shared cross-encoder (`bge-reranker-v2-m3`) with an authority tie-break ([retrieve spec](specs/retrieve.md)). Hierarchy expansion was removed (DF-27).

## AD-09: The bridge aligns identities only

The bridge connects specification signals/sections to likely RTL signals/modules. A link
means “inspect this RTL evidence for the same architectural concept.” It never means that a
behavior is implemented correctly.

Every accepted link includes methods, component scores, confidence band, candidate rank,
and both-domain provenance. Rejected choices remain inspectable as unresolved candidates.

## AD-10: Bridge matching is layered and conservative

Signal matching proceeds from cheap multi-candidate name evidence to weighted structural
evidence. Full builds semantically rerank only structural survivors. Optional local-model
resolution permits one bounded ambiguous-shortlist call and may abstain.

Current structural inputs are name, width, direction, section/module relation,
description/usage, and table context. Accepted confidence bands are `WEAK`, `MEDIUM`, and
`STRONG`; below-threshold or ambiguous rows are `UNRESOLVED`.

Module matching uses normalized title/name evidence, context, and accepted child-signal
support. Close or weak module choices are likewise unresolved.

> **Superseded on branch `simplify`:** Weighted structural scoring, the `WEAK` band, semantic rerank, and the local-model resolver were removed. Links are `identity` (STRONG) or `interface_prefix` (MEDIUM; `same_net` was dropped, all 19 of its links were wrong); widths are stated, never filters ([bridge spec](specs/bridge.md), DF-14 to DF-19).

## AD-11: Unresolved is not missing

`UNRESOLVED` means the alignment layer could not select a trustworthy identity. It is not a
claim that RTL lacks a behavior. Even a strong identity link supplies only a navigation
target.

A bridge build with zero accepted alignments is valid. The comparison path still searches
both RAGs from the original question.

Only final reasoning over retrieved specification and RTL evidence may report implemented,
partially implemented, missing, contradictory, or insufficient evidence.

## AD-12: Exactly three explicit query routes

The deterministic router emits `SPEC`, `RTL`, or `COMPARISON`.

- `SPEC` runs specification retrieval, hierarchy expansion, merge, and answer.
- `RTL` runs RTL retrieval, merge, and answer.
- `COMPARISON` runs specification retrieval, structured-signal selection, bridge lookup,
  targeted RTL retrieval, surrounding-block selection, merge, and answer.

LangGraph edges enforce these sequences. Model tool choice cannot bypass a required lane.

> **Superseded on branch `simplify`:** Every question now runs one linear graph through both lanes (`classify → retrieve_spec → retrieve_rtl → lookup_bridge → merge → answer`); the route decides which domains the answer must cite ([agent spec](specs/agent.md)).

## AD-13: Comparison requires evidence from both domains

The comparison answer node checks that specification and RTL evidence exist. When either is
empty it skips the model and returns `INSUFFICIENT EVIDENCE`.

Bridge absence does not block comparison: the RTL retriever can still search the original
question. An available bridge target may narrow and enrich RTL retrieval, but never replaces
it.

## AD-14: Give the local model one bounded evidence package

The answer model receives only the question, route, selected cited evidence, relevant
alignments, and targeted RTL blocks. Default caps are 12,500 total characters within a
4,096-token model context, with separate specification, RTL, and bridge budgets.

The prompt requires source citations and explicitly distinguishes identity alignment from
behavioral proof. If evidence is oversized, deterministic visible truncation occurs before
the model call.

The optional bridge resolver has its own smaller cap: three candidates, 2,600 input
characters, and 96 output tokens by default.

> **Superseded on branch `simplify`:** Character budgets and truncation were removed: the model receives whole passages within `AGENT_NUM_CTX` (8,192 tokens); the bridge resolver no longer exists ([agent spec](specs/agent.md)).

## AD-15: Generated state is reproducible and inspectable

All derived artifacts live under `output/`. Source-derived stable IDs and content hashes
prevent duplicate records. Domain embedding manifests bind each vector row to its current
embedding surface, allow unchanged indexes to skip encoder work, and are validated before
query-time retrieval. Bridge artifacts retain input fingerprints, deterministic ordering,
and atomic publication.

The canonical clean rebuild is:

```bash
python src/build.py --clean
```

The build validates the cleanup target before deletion and executes each stage as an
isolated process in dependency order.

> **Superseded on branch `simplify`:** Only the specification index is embedded and manifested; RTL records are not embedded (see AD-06).

## AD-16: Debug state is a public contract

Every agent run retains structured trace events for routing, specification retrieval,
bridge lookup, RTL retrieval/block targeting, evidence merge, and answer invocation. The CLI
prints this trace separately from the answer.

Counts, stable IDs, signal targets, confidence, RTL file/line ranges, merged-context size,
and empty-lane abstention are observable without relying on model narration. The trace does
not expose the evidence body or hidden model reasoning.

## AD-17: Evaluate retrieval and orchestration, not only prose answers

Hermetic tests cover schemas, stable IDs, exact-first ranking, manifests, conservative
alignment, route isolation, mandatory comparison lanes, bounded context, and rebuild safety.

The current-schema end-to-end benchmark reports:

- specification and RTL Recall@K and MRR;
- bridge Recall@K, MRR, and Top-1 accuracy;
- three-route classification accuracy;
- comparison both-domain retrieval rate;
- citation completeness, answer outcomes, and latency.

Commands:

```bash
python -m pytest -q -p no:cacheprovider tests
python src/bench.py
```
