# Pipeline operations

This guide describes the current implementation. Historical experiments live in
[docs/history/pre-remediation](docs/history/pre-remediation/ARCHITECTURE.md).

## Build and publication

Run `.venv/bin/python src/build.py --clean` from the repository root. Each build
uses a fresh `output/generations/<id>/` directory; `--clean` is retained as a
compatibility spelling and never removes the active corpus. Stages run as isolated
processes, in this order:

1. `ingest.py`: PDF hierarchy, structured tables and specification chunks.
2. `spec_signals.py`: conservative table-derived port/register records.
3. `embed.py`: specification vectors in Chroma and a versioned embedding manifest.
4. `rtl_ingest.py`: Verilator declarations, source text, constructs and hierarchy.
5. `rtl_embed.py`: lexical RTL search records and separate original-source excerpts.
6. `bridge.py`: deterministic identity and confirmed-address-map links.

The build compares source hashes before and after all stages, validates the index,
and writes `generation.json` with input hashes, configuration, package versions,
model revisions and the compiler version. Only a successful complete build atomically
switches the `output/current` symlink. Failed generations remain for diagnosis and
the previous corpus stays active. Existing processes retain their resolved generation;
restart them deliberately to adopt a new one.

`build.py --no-embeddings` creates an inspection-only generation and never promotes
it for serving. It does not claim compatibility with an old index.

## Scratch stages and experiments

Published corpus files are read-only to stage entrypoints. To run a stage directly,
choose an explicit scratch location before starting Python:

```bash
export CHIPVER_OUTPUT_DIR=output/scratch
.venv/bin/python src/ingest.py
.venv/bin/python src/spec_signals.py
.venv/bin/python src/embed.py
.venv/bin/python src/rtl_ingest.py
.venv/bin/python src/rtl_embed.py
.venv/bin/python src/bridge.py
unset CHIPVER_OUTPUT_DIR
```

Benchmark reports may be added to the selected generation; they are not corpus
artifacts. Mutable Chroma storage files are checked through their logical inventory,
text and vector dimensions instead of byte hashes. JSON corpus artifacts are sealed.
Keep source PDFs and model caches outside disposable scratch directories.

## Inputs

PDFs live in `Ingest/`; normalized document IDs must be unique. The default RTL
source root is `rtl/`, with recursive discovery and explicit `RTL_TOP_MODULE` when
automatic top selection is ambiguous. For ordered packages, includes, defines,
and parameter overrides, set `RTL_PROJECT` to a JSON manifest:

```json
{"top":"chip","sources":["packages/defs.sv","core/chip.sv"],
 "include_dirs":["include"],"defines":{"FEATURE":1},"parameters":{"WIDTH":32}}
```

Paths resolve inside `rtl/`; source files must exist and retain their subdirectories.
Compiler errors or nonzero exits fail ingestion. Lint warnings are retained as
input-design diagnostics; accepting a design for evidence extraction does not
certify that design as correct.

## Query execution

In default `all` mode, every question follows `classify -> retrieve_spec -> retrieve_rtl -> lookup_bridge
-> merge -> answer`. The requested intent controls required answer domains, not
which retrieval lanes run. There is no query recovery, graph expansion, bridge LLM
resolver, RTL vector index, or semantic bridge reranking.

On the experimental branch, `CHIPVER_RETRIEVAL_MODE=kev` replaces the regex router
with a local Kev decision and skips the unused retrieval lane. Comparisons and
router fallbacks search both. See [setup and the paired benchmark](docs/KEV_RETRIEVAL_EXPERIMENT.md).

Specification candidates are BM25 plus Chroma dense hits, reranked by one shared
cross-encoder with a document-authority adjustment. RTL uses compiler-identifier
exact hits, useful constructs, BM25 and that shared reranker. RTL summaries are
search-only; answers see bounded original-source excerpts with full relative paths.

The merge measures complete chat messages with the pinned Qwen tokenizer and
removes whole passages until the 8,192-token window has room for the configured
1,024-token answer. Removed passages do not authorize citations. Exact quote/schema
validation follows generation; comparisons remain explicitly unverified.

## Service and evaluation

See [README.md](README.md) for installation, local API and test commands, and
[docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) for human review. Use one API
worker per local GPU. Readiness requires a sealed, compatible corpus and exact local
model identity. Cold initialization is synchronized; only one API inference runs
at a time. Missing/stale artifacts return 503, as does exhausted inference capacity.

Rollback means stopping the reader, selecting a previously verified generation with
`artifacts.publish_generation`, and restarting. Never edit a published generation
or repoint `current` to an incomplete parser-only build. Retain source documents,
model revisions and old generations according to your local storage policy.
