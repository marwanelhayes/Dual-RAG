# Current architecture

ChipVer has four responsibilities: discover source structure, retrieve evidence,
align identities, and validate source-excerpt answers. [CONTEXT.md](../CONTEXT.md)
defines the domain terms; [PIPELINE.md](../PIPELINE.md) documents commands.

## Offline corpus

PDF ingestion preserves hierarchy, page provenance and structured table rows.
Sentence windows stay within sections; table windows retain whole rows and repeat
column headings. Both include breadcrumbs and must fit the embedding encoder.
Figures, waveforms and scanned-page vision are outside the active PDF backend.

Signal extraction admits structured signal/port and register-address tables.
Verilator supplies RTL declarations, widths, hierarchy, bindings and constructs.
Original identifiers and paths survive normalization. The bridge links identities
and confirmed table/enum address maps, preserving unresolved candidates rather
than turning a retrieval miss into a claim of absence.

Specification retrieval uses BM25 plus Chroma vectors and a shared cross-encoder.
RTL uses exact metadata lookup and lexical candidates with the same reranker;
RTL records are not embedded. Generated summaries improve search; separate bounded
source excerpts preserve original RTL lines for citations.

## Artifact lifecycle

A full build runs in a new directory, validates source stability and index
compatibility, then publishes one generation by atomically replacing `current`.
Readers resolve it once. A generation manifest seals corpus JSON and records the
source, environment and model identities. The vector manifest additionally binds
encoder revision, prompts, normalization, library versions, dimension, record IDs
and indexed text. Old and parser-only generations are never silently mixed.

## Online execution

The graph is linear: classify, retrieve both lanes, look up bridge identities,
merge evidence, then answer. Intent determines required citable domains. A bridge
link never establishes behavioral conformance. Prompt measurement uses the actual
answer tokenizer and reserves output tokens. Only whole passages survive budgeting.

The native answer model selects up to three visible source labels through a bounded
JSON schema; the application copies their original passages into canonical claims.
Validation checks exact visible-source containment with whitespace normalization,
known locations and required-domain coverage. Rendered answers contain only validated
quotes and fixed policy text. These checks do not establish relevance or completeness.

## Service boundary

One runtime owns lazy adapters. A reentrant lock prevents duplicate cold initialization.
The default API preflight verifies the sealed corpus, real index, local model digest,
retrievers, tagger and answer tokenizer. Readiness follows successful preflight.
A single inference slot bounds GPU use; use one service worker per GPU. The default
operating scope is trusted localhost, without authentication or multi-tenant isolation.

## Evaluation boundary

Fast contract tests use explicit deterministic substitutes. Opt-in live tests use
real models and do not certify answer quality. Development retrieval metrics,
reviewed answer quality and behavioral hardware verification are separate claims.
The held-out candidate remains unapproved until independent review is recorded.
