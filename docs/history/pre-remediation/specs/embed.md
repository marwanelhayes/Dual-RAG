> Historical record archived on 2026-10-19. Commands, metrics, and descriptions below are not the current contract. See [current architecture](../../../ARCHITECTURE.md).

# Spec: `src/embed.py`

Decided 2026-09-23 in the simplification pass. Deferred options live in
[`docs/DEFERRED.md`](../DEFERRED.md) (DF-08 Chroma metadata filters, DF-09 multi-vector long
chunks).

## Domain

`embed.py` turns chunk records into a searchable vector index. It owns three things:
the encoder (`Embedder`), the store (`VectorStore`), and the index's freshness manifest.
The RTL lane is ranked lexically and has no index (`retrieve/rtl-lexical-only`).

Non-goals: chunk sizing and token caps (ingest steps 13-14 own them), metadata truth
(`chunks.jsonl` / `rtl_chunks.jsonl` own it), retrieval fusion and reranking (`retrieve.py`).

## What embed must do

1. **Embedded text** = `context_header + "\n\n" + text`. The header is the section breadcrumb
   (median 39 bge tokens, max 78); the printed heading is no longer repeated in `text`
   (ingest step 9b).
2. **Encoder.** `BAAI/bge-small-en-v1.5` (512 tokens, 384 dims) on `cuda`, falling back to
   CPU (`available_device`). Passages are encoded bare; queries get `EMBED_QUERY_PREFIX`
   (BGE asymmetric convention). Vectors are always L2-normalised (no config knob).
3. **Store: Chroma.** One persistent Chroma directory (`output/spec_index`) with one collection `chunks`, `space="cosine"`, embeddings
   supplied by `Embedder` (no Chroma embedding function, telemetry off). Each entry holds
   `id` (= `chunk_id`), the embedding, and the embedded text only. One directory per index
   (not one client with two collections) keeps "index dir exists" meaning "this index was
   built" for the freshness guards.
4. **Hits are hydrated.** `VectorStore.search` returns `(Document, score)` pairs; the
   `Document` is built from the caller's records by id, so metadata is never duplicated in
   the store. Score = `1 - distance` (cosine similarity).
5. **Every chunk is encoded.** No duplicate-text dedup (2 duplicates in 5363 chunks).
6. **Manifest.** `chunk_id -> content_hash(context_header, text)`, written beside the index.
   The index is stale only when embedded text changes; metadata-only changes keep it fresh.
   `main` always embeds (no skip-if-unchanged).
7. **Over-limit chunks** (52 at step 9b, all prose, mostly Sail code / hex tables) are
   truncated by the encoder; `ingest.chunks_over_limit` tracks them until ingest step 14.

## Removed

| Item | Why |
|---|---|
| dedup in `VectorStore.build`, `chunk_fingerprint`, metadata `fingerprint` | saves 2 of 5363 encodes |
| `record_fingerprint` + `"dual-rag-index-v2"` salt | whole-record hash; metadata no longer lives in the index |
| skip-if-unchanged in `main` | `build.py --clean` wipes `output/`, so it never fires |
| AXI smoke query in `main` | asserts nothing; `bench.py --retrieval` covers retrieval |
| `EMBED_NORMALIZE` | one valid value |
| `langchain_core.embeddings.Embeddings` base | existed only for FAISS query dispatch |
| FAISS docstore pickle, `allow_dangerous_deserialization` | Chroma + hydration |
| `faiss-cpu`, `langchain-community` | replaced by `chromadb` |

Moved: `content_hash`, `load_chunks` → `ingest.py` (they hash and read what ingest writes;
earlier stages no longer import a later one).

## Pending consolidation

- `langchain_core.documents.Document` stays as the hit type until the `retrieve.py` walk.
- The manifest stays because `agent.py:1319` (spec) and `rtl_embed.index_manifest_status`
  (RTL) refuse stale indexes; revisited at the `agent.py` / `rtl_embed.py` walks.
- `VectorStore.load` takes the records to hydrate from; its one call site is
  `retrieve.Retriever.load`, which both the spec and RTL paths use.

## Metrics

Decision metric unchanged: `retrieval.spec.section_recall_at_k` (0.633 at step 9b). RTL
guard: `retrieval.rtl.section_recall_at_k` 1.0, RTL MRR 0.584. Chroma's HNSW search is
approximate; any per-question change at e5 is explained before continuing.

## Execution queue

Every step: failing test first, full suite green, commit, `build.py --clean`,
`bench.py --retrieval`, `track.py --tag <tag>`.

| Step | Tag | Change | Expected diff |
|---|---|---|---|
| e0 | `embed/move-shared-helpers` | `content_hash`, `load_chunks` → `ingest.py`; update imports | zero |
| e1 | `embed/drop-smoke-query` | remove AXI query from `main` | zero |
| e2 | `embed/drop-dedup` | remove dedup, `chunk_fingerprint`, metadata `fingerprint` | zero bench |
| e3 | `embed/text-manifest` | manifest = `content_hash(header, text)`; drop skip + `record_fingerprint`; flip `test_build.py:73` | zero bench |
| e4 | `embed/drop-normalize-knob` | remove `EMBED_NORMALIZE` | zero |
| e5 | `embed/chroma` | Chroma `spec` + `rtl` collections, hydrated hits, drop `Embeddings` base, drop `faiss-cpu` + `langchain-community` | spec 0.633 / rtl 1.0 held |
