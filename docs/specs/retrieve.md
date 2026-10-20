# Retrieval contract

`src/retrieve.py` builds BM25 candidates for each lane. The specification lane unions
BM25 IDs with Chroma dense candidates; a shared revision-pinned cross-encoder reranks
them, with the configured document-authority adjustment. There is no specification
exact-first lane or reciprocal-rank fusion.

CUDA reranker weights load directly in float16; CPU loading keeps its default
precision. This avoids a full-precision CUDA allocation before conversion. See
the [GPU memory experiment](../GPU_MEMORY_EXPERIMENT.md) for paired measurements.

RTL uses compiler-identifier exact lookup, useful constructs related to named signals,
then lexical/reranked candidates. RTL has no dense index. Continuation windows can
report their base record ID while retaining the matched window's source excerpt.
Neither retrieval scores nor exact names establish behavioral correctness.

Startup validates the specification index and immutable corpus. Runtime lazy loading
is synchronized and the API admits one inference at a time. The regular suite uses
deterministic ranking adapters; live retrieval measurements must record model, corpus
and configuration identities. See [the architecture](../ARCHITECTURE.md).
