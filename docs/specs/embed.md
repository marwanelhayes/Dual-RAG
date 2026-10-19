# Specification embedding contract

`src/embed.py` encodes `context_header + blank line + text` using the configured,
revision-pinned SentenceTransformer. Passages and queries are normalized; each uses
its configured prefix. Chroma stores IDs, vectors and search text; result metadata
is hydrated from the matching corpus records.

Every build embeds the complete selected corpus. There is no incremental skip path.
`embed_manifest.json` schema 2 records the chunk fingerprints, embedding model/revision,
query/document prefixes, text-only mode, normalization, encoder-library versions and
vector dimension. Freshness requires an actual readable collection with the exact
record IDs, indexed text and compatible vector dimensions. Legacy text-only manifests
require a rebuild.

The complete build stages the index before publication. Mutable Chroma storage is
validated logically; corpus JSON and the embedding manifest are sealed. RTL records
are lexical and have no vector index. Tests cover actual persisted Chroma collections
with deterministic test vectors. See [pipeline operations](../../PIPELINE.md).
