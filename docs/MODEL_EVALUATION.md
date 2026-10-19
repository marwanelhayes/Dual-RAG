# Model and deterministic-extraction evaluation

Research date: 2026-10-19. Published general benchmarks shortlist candidates; they do
not establish a winner on hardware specifications, RTL or this 4 GB GPU.

| Role | Current implementation | Candidate or comparison | Decision evidence required |
| --- | --- | --- | --- |
| Source structure | PDF geometry/text plus Verilator | Qianfan-OCR only as an optional Markdown experiment | Original identifier fidelity, table/header recovery, runtime and memory on representative pages |
| Identity links | Deterministic names, supported interface prefixes, confirmed enum addresses | Semantic suggestions kept unresolved | Reviewed identity precision/recall; never infer behavioral equivalence |
| Question/evidence signal selection | Deterministic source-identifier matching | Replaced pinned GLiNER2-base-v1 | Paired 60-question development comparison preserved retrieval metrics and reduced warm latency; see results below |
| Specification embeddings | Pinned BGE-small-en-v1.5 | Qwen3-Embedding-0.6B | Section recall, context fact coverage, build time and peak memory |
| Candidate reranking | Pinned BGE-reranker-v2-m3 | Qwen3-Reranker-0.6B | Same-corpus recall, inference latency, prompt/API compatibility and memory |

Primary references:

- [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
  documents a 0.6B instruction-aware embedding model, a 32K context and configurable
  vector dimensions. Its query instructions differ from BGE's prefix convention.
- [Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)
  documents a causal-model reranking interface. It is not automatically interchangeable
  with the current cross-encoder adapter.
- [BGE-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
  and [GLiNER2-base-v1](https://huggingface.co/fastino/gliner2-base-v1)
  document the reranker and former selector model families and intended APIs.

Keep comparisons isolated by generation and dataset hashes. Change one component
at a time, record cold/warm latency and memory, rerun multiple times, and retain the
simpler implementation when quality gains do not justify resource cost. Do not tune
on the independent held-out candidate. The deterministic selector was retained after
the measured comparison below. OCR and alternative embedding/reranker comparisons
remain uncompleted.

## Measured selector replacement

The [selector experiment](SIGNAL_SELECTION_EXPERIMENT.md) compares real GLiNER2
selection with source-identifier matching on the same corpus and 60 development
questions. Every question retained identical retrieval and expected-evidence coverage.
Warm mean retrieval time, excluding the shared cold start, fell from 2.810 to
2.592 seconds (7.7%). A separate repeated selector measurement fell from 196 ms
to 0.64 ms, and the GLiNER2 model's 438 MB CUDA allocation is no longer needed.
These measurements justify removing the selector model; they do not measure
independent semantic precision or establish that regex is best for all corpora.

## Real-model integration observation

The initial two-question smoke run passed the WFI source request but the subsequent
RTL request returned invalid JSON and was rejected. This is why unit passes are
not treated as model quality evidence. A bounded quote schema then produced an altered
quote, also correctly rejected. The native adapter now selects only source labels;
the application copies original passages exactly and rejects provider truncation.
The resulting two-question real-model smoke passed in 63 seconds, with about
20 seconds per model call. This establishes integration and source integrity on
those requests only; it does not establish independent answer accuracy.

The expanded ten-question run subsequently exposed six missing-domain rejections.
Both source domains were available; the provider schema allowed incomplete domain
coverage. The native schema now requires the route's domains or an empty abstention.
See [the native schema experiment](ANSWER_SCHEMA_EXPERIMENT.md) for the real-provider
replay, compatibility finding and final benchmark results. The live suite now
includes a comparison request to cover this failure mode.
