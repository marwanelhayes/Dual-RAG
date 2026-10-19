> Historical record archived on 2026-10-19. Commands, metrics, and descriptions below are not the current contract. See [current architecture](../../../ARCHITECTURE.md).

# Spec: `src/retrieve.py`

Decided 2026-09-26 in the simplification pass. Deferred options live in
[`docs/DEFERRED.md`](../DEFERRED.md) (DF-20 to DF-24).

## Domain

`retrieve.py` turns one question into ranked evidence from one domain-local index.

- **Spec**: `Retriever` — BM25 ∪ dense (Chroma) candidates, cross-encoder rerank shifted by
  document authority.
- **RTL**: `RTLRetriever` — exact lookup on compiler-stated names (module / function / task /
  signal), constructs that read or write a matched signal, then `Retriever` fill from BM25
  candidates only (no RTL vectors, `retrieve/rtl-lexical-only`), `:wN` windows folded into
  their record.

Non-goals: query rewriting, recovery, lane floors, bridge expansion (agent); building
records or indexes (ingest, embed, rtl_embed).

## Baseline (2026-09-26, head `0dc61b9`)

- Agent-level: `retrieval.spec.section_recall_at_k` 0.675, `retrieval.rtl.exact_id_recall_at_k`
  0.458, RTL MRR 0.625. These include agent over-fetch, dedup, hierarchy expansion and
  recovery, so they do not isolate this file.
- Spec exact lane: fires on 19/40 spec questions, gold section in its set on 16; sets of
  12-467 chunks (`MODE` 467, `RISC` 353, `RV32I` 116).
- RTL exact lookup: fires 18/40, gold in its top 5 on 15. Structure pass on 13 signal
  questions: gold hits 13 → 21 (k = 5, no dense).
- Match tiers over 40 RTL questions: case-exact records 6, case-exact signals 63, phrase 2
  (all useful); casefold 7 (all wrong); normalized 0.
- Document authority: waveform 0.479, AXI spec 0.856, riscv 0.89 → about −0.4 logit on
  waveform chunks.

## What retrieve must do

1. **Spec `Retriever.search(q, top_k)`**: `dict.fromkeys(bm25[:30] + dense[:30])` →
   `Reranker.rank` → top_k. No exact lane, no pinning, no RRF.
2. **`Reranker`**: `ms-marco-MiniLM-L-6-v2`; `rank` = cross-encoder logits +
   `apply_authority` (centered, weight 1.0); `score_pairs` for agent.
3. **Authority** stays inferred: `roles.document_role_scores` → `ROLE_AUTHORITY`.
4. **`exact_lookup`**: query terms = every `_IDENTIFIER` match; record names matched
   case-exactly against `lookup_terms`; signal names case-exactly against `signal_names`, only
   for mixed-case or non-alpha terms; phrase match on `normalized_name`. A record the query
   names outranks one that only shares a signal. Hits annotated `exact_match_type`,
   `symbol_name`.
5. **`RTLRetriever.search(q, top_k)`**, one rule: signal-matched modules interleaved with
   their structures (writer blocks first), then remaining exact hits, then `Retriever` fill,
   then owned constructs of retrieved modules reranked with exact ids pinned to top half.
   Structures are never discarded.
6. **Hit shape**: both retrievers return `[(Document, score)]`; score `None` for exact and
   structure hits.

Record fields read (from `rtl_chunks.jsonl`): `chunk_id`, `context_header`, `text`,
`source_type`, `module`, `record_id`, `start_line`, `lookup_terms` (`[name]`),
`normalized_name`, `signal_names`, `signals_read`, `signals_written`.

## Removed

| Item | Why |
|---|---|
| `IDENTIFIER_RE`, `CONTEXT_TERMS`, `identifier_terms`, `exact_document_ids`, `exact_terms`, spec pinning, `top_k == 1` path | DF-20 |
| RRF in `Retriever.candidates` | reranker scores every candidate; order is ties only |
| casefold + normalized tiers in `_term_strength` | DF-23 |
| `_QUERY_STOP_WORDS`, `len >= 3` | DF-24 |
| regimes 1-3 in `RTLRetriever.search`, `wanted == 1` split | one ordering rule |
| module `search()`, `load_records()`, `RTLRetriever(store=)`, `search(k=)`, `main()`, `Retriever.load(records=None)` | 0 production callers |
| hit metadata `roles`, `matched_signal` | 0 readers |
| `rtl_embed`: `lookup_terms` original/normalized copies, `_LOOKUP_TERMS`, record `original_name`, `signal_metadata` (14 keys) → `signal_names` | only `original_name` read |

Moved: `test_model_device_preserves_cpu_and_falls_back_without_cuda` → `tests/test_embed.py`.

## Pending consolidation (agent walk)

Resolved at the agent walk ([`agent.md`](agent.md)): RRF and `RETRIEVE_RRF_K` go with query
recovery (a1); `Document` stays the hit type (`langgraph` needs `langchain-core`); the
`VectorStore.load(records)` duplicate documents dict stays (embed/retrieve, not agent).

## Metrics (`track.py`)

Decision: `retrieve.spec.section_recall_at_k`, `retrieve.rtl.exact_id_recall_at_k` (raw question
→ retriever, k = 5, same gold and scorers as `bench`). Guards: agent-level `retrieval.*`,
`retrieve.*.mrr_at_k`.

## Execution queue

Every step: failing test first, full suite green, commit, `build.py --clean`,
`bench.py --retrieval`, `track.py --tag <tag>`. Experiments run on throwaway branches
`exp/<name>` from the q0 commit, committed (clean tree), kept unmerged.

| Step | Tag | Change | Expected diff |
|---|---|---|---|
| q0 | `retrieve/metrics` | stage-local `retrieve.spec.*` / `retrieve.rtl.*` in bench + track | metrics only |
| exp | `q-exp/no-rerank` | final order = RRF, no cross-encoder | experiment |
| exp | `q-exp/no-owned` | no owned-construct rerank | experiment |
| q1 | `retrieve/drop-dead-code` | dead entry points, `k` alias, `store=` seam, `main`, annotations, device test move | zero |
| q2 | `retrieve/union-not-rrf` | candidates = union | zero (ties) |
| q3 | `retrieve/drop-spec-exact-lane` | Q2 | `retrieve.spec` |
| q4 | `retrieve/case-exact-tiers` | Q8 | CMP_ROUNDING, DYN_ROUNDING, MTVEC |
| q5 | `retrieve/query-terms` | Q9 | watch SPEC_PRIV_MCAUSE |
| q6 | `retrieve/one-ordering` | Q10 | FORWARD_AE, TRAP_ENTRY, SIGNIMMD |
| q7 | `rtl_embed/lookup-fields` | Q11 | `rtl_chunks` minus / renamed keys; texts identical |
| q8 | `retrieve/hit-shape` | `RTLRetriever` returns `(Document, score)` | zero |

If `q-exp/no-rerank` ≥ q0 on `retrieve.spec`, reopen the reranker as its own question; if
`q-exp/no-owned` ≥ q0 on `retrieve.rtl.exact_id_recall_at_k`, add a removal step.

## Outcome (2026-09-26)

All steps run; commits and MLflow runs in [`docs/NEXT_STEPS.md`](../NEXT_STEPS.md).

| | q0 | q8 |
|---|---|---|
| `retrieve.spec.section_recall_at_k` | 0.683 | 0.683 |
| `retrieve.rtl.exact_id_recall_at_k` | 0.508 | 0.508 (0.517 at q6) |
| `retrieve.rtl.mrr_at_k` | 0.630 | **0.649** |
| agent `retrieval.spec.section_recall_at_k` | 0.675 | **0.683** |
| agent `retrieval.rtl.exact_id_recall_at_k` | 0.458 | **0.500** |
| agent `retrieval.rtl.mrr_at_k` | 0.625 | **0.666** |
| `src/retrieve.py` lines (`rtl_embed.py`) | 641 (379) | 453 (362) |

- Experiments confirm both keeps: without the reranker stage spec is 0.493; without the owned
  rerank stage RTL exact is 0.475.
- q7 lost `CMP_MINFO_CSR_EXACT`'s structure (signal `CsrFile` had matched via its
  `normalized_name`, which Q11 missed); accepted, DF-25.
