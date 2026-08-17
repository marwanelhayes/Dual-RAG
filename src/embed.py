"""Embed ingested chunks once and persist their FAISS similarity index.

Run ``python src/embed.py`` to transform ``output/chunks.jsonl`` into the FAISS store
and incremental manifest configured in ``configure.py``.
"""

import hashlib
import json

import configure as cfg

from langchain_core.documents import Document
# Subclassing Embeddings makes FAISS dispatch queries through embed_query.
from langchain_core.embeddings import Embeddings


def available_device(requested, cuda_available=None):
    """Return the requested device, falling back to CPU when CUDA is unavailable.

    Args:
        requested: Device name requested for the embedding model.
        cuda_available: Known CUDA availability, or None to detect it with PyTorch.

    Returns:
        The requested value unchanged, except unavailable CUDA requests fall back to
        ``"cpu"``.
    """
    if not str(requested).lower().startswith("cuda"):
        return requested
    if cuda_available is None:
        try:
            import torch

            cuda_available = torch.cuda.is_available()
        except (ImportError, RuntimeError):
            cuda_available = False
    return requested if cuda_available else "cpu"


def content_hash(*parts):
    """Fold several strings into one short, stable hex fingerprint.

    Args:
        *parts: the strings to fingerprint together, in order.

    Returns:
        a 16-character hex digest that is identical across runs and machines.
    """
    # NUL delimiters distinguish partitions such as ("ab", "c") and ("a", "bc").
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    # A readable 64-bit prefix is ample for this corpus's few thousand chunks.
    return h.hexdigest()[:16]


def chunk_fingerprint(record):
    """Fingerprint the content of a single chunk.

    Args:
        record: one chunk dict as written to chunks.jsonl.

    Returns:
        the content hash of everything that goes into the chunk's embedding.
    """
    # Breadcrumb changes alter embeddings, so they must invalidate the manifest.
    return content_hash(record["context_header"], record["text"])


def record_fingerprint(record):
    """Fingerprint embedded text and the metadata persisted beside its vector.

    Args:
        record: One spec or RTL retrieval record.

    Returns:
        Stable fingerprint for index freshness and rebuild decisions.
    """
    material = json.dumps(record, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return content_hash("dual-rag-index-v2", material)


def load_chunks(path=cfg.CHUNKS_PATH):
    """Read the chunk records the ingest stage wrote.

    Args:
        path: the JSONL file to read.

    Returns:
        list of chunk dicts, one per line.
    """
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_manifest(records):
    """Map every chunk id to its content fingerprint.

    Args:
        records: the chunk dicts from load_chunks.

    Returns:
        dict of chunk_id -> content hash for the whole corpus.
    """
    return {r["chunk_id"]: record_fingerprint(r) for r in records}


def record_to_document(record):
    """Turn one chunk record into a LangChain Document.

    Args:
        record: one chunk dict from chunks.jsonl.

    Returns:
        a langchain_core Document whose page_content is what gets embedded and whose
        metadata is the chunk's full provenance minus the raw text.
    """
    # Breadcrumbs give context-free paragraphs their semantic neighborhood.
    page_content = record["context_header"] + "\n\n" + record["text"]

    # Keep provenance as metadata while page_content owns the embedded text.
    metadata = {k: v for k, v in record.items() if k != "text"}
    metadata["fingerprint"] = chunk_fingerprint(record)
    return Document(page_content=page_content, metadata=metadata)


class Embedder(Embeddings):
    """Encode passages and queries through LangChain's Embeddings protocol."""

    def __init__(self, model=cfg.EMBED_MODEL, device=cfg.EMBED_DEVICE,
                 batch=cfg.EMBED_BATCH, normalize=cfg.EMBED_NORMALIZE):
        """Load the encoder and move it onto the target device.

        Args:
            model: sentence-transformers model name.
            device: "cuda" for the GPU, "cpu" for the fallback.
            batch: chunks per forward pass.
            normalize: whether to L2-normalise the output vectors.

        Returns:
            None.
        """
        # Defer the multi-gigabyte model stack until embedding is requested.
        from sentence_transformers import SentenceTransformer

        self.batch = batch
        self.normalize = normalize
        # Move weights once so later batches avoid repeated host-to-device transfers.
        self.device = available_device(device)
        self.model = SentenceTransformer(model, device=self.device)

    def embed_documents(self, texts):
        """Encode a list of passages into vectors.

        Args:
            texts: the passage strings to embed.

        Returns:
            list of vectors, one per passage, as plain Python floats.
        """
        # BGE embeds passages bare; progress exposes the pipeline's slow stage.
        vecs = self.model.encode(
            texts,
            batch_size=self.batch,
            normalize_embeddings=self.normalize,
            show_progress_bar=True,
            convert_to_numpy=True,
        )
        return vecs.tolist()

    def embed_query(self, text):
        """Encode a single search query into a vector.

        Args:
            text: the raw user query.

        Returns:
            one vector as a list of plain Python floats.
        """
        # BGE's asymmetric convention applies the instruction prefix only to queries.
        vec = self.model.encode(
            cfg.EMBED_QUERY_PREFIX + text,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
        )
        return vec.tolist()


class VectorStore:
    """Expose construction, persistence, and search for the corpus FAISS index."""

    def __init__(self, faiss, embedder):
        """Wrap an already-constructed FAISS index.

        Args:
            faiss: a langchain_community FAISS instance.
            embedder: the Embedder used to build it, reused for queries.

        Returns:
            None.
        """
        self._faiss = faiss
        self.embedder = embedder

    @classmethod
    def build(cls, records, embedder=None):
        """Embed the chunks once and pack them into a fresh FAISS index.

        Args:
            records: the chunk dicts from load_chunks.
            embedder: an Embedder to reuse, or None to construct the default one.

        Returns:
            a VectorStore holding the built index.
        """
        from langchain_community.vectorstores import FAISS
        from langchain_community.vectorstores.utils import DistanceStrategy

        embedder = embedder or Embedder()
        documents = [record_to_document(r) for r in records]

        # Deduplicate only encoder work; the index retains every provenance-bearing chunk.
        unique = {}
        for doc in documents:
            unique.setdefault(doc.metadata["fingerprint"], doc.page_content)
        fingerprints = list(unique)
        vectors = embedder.embed_documents([unique[fp] for fp in fingerprints])
        by_fingerprint = dict(zip(fingerprints, vectors))

        text_embeddings = [
            (document.page_content, by_fingerprint[document.metadata["fingerprint"]])
            for document in documents
        ]
        metadatas = [d.metadata for d in documents]
        # Reuse stable chunk IDs so future incremental updates can address entries.
        ids = [d.metadata["chunk_id"] for d in documents]

        # Inner product equals cosine similarity for the normalized BGE vectors.
        faiss = FAISS.from_embeddings(
            text_embeddings=text_embeddings,
            embedding=embedder,
            metadatas=metadatas,
            ids=ids,
            distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
        )
        return cls(faiss, embedder)

    def save(self, index_dir=cfg.INDEX_DIR):
        """Write the index to disk so retrieval can load it without re-embedding.

        Args:
            index_dir: directory to write the FAISS files into.

        Returns:
            None.
        """
        self._faiss.save_local(str(index_dir))

    @classmethod
    def load(cls, index_dir=cfg.INDEX_DIR, embedder=None):
        """Load a saved index back for querying.

        Args:
            index_dir: directory a previous run saved the index into.
            embedder: an Embedder to reuse, or None to construct the default one.

        Returns:
            a VectorStore ready to search.
        """
        from langchain_community.vectorstores import FAISS

        embedder = embedder or Embedder()
        # Deserialization trusts only the index written into this project's output folder.
        faiss = FAISS.load_local(
            str(index_dir), embedder, allow_dangerous_deserialization=True)
        return cls(faiss, embedder)

    def search(self, query, k=5):
        """Return the k chunks nearest a query, with their similarity scores.

        Args:
            query: the raw user question.
            k: how many neighbours to return.

        Returns:
            list of (Document, score) pairs, nearest first.
        """
        return self._faiss.similarity_search_with_score(query, k=k)


def read_manifest(path=cfg.MANIFEST_PATH):
    """Load the chunk_id -> hash map from the previous run.

    Args:
        path: the manifest file to read.

    Returns:
        the saved map, or an empty dict if this is the first run.
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_manifest(manifest, path=cfg.MANIFEST_PATH):
    """Persist the chunk_id -> hash map beside the index.

    Args:
        manifest: the map from build_manifest.
        path: where to write it.

    Returns:
        None.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


def main():
    """Embed the corpus into FAISS, skipping the whole job when nothing has changed.

    Returns:
        None.
    """
    records = load_chunks()
    if not records:
        raise SystemExit(f"no chunks in {cfg.CHUNKS_PATH}; run src/ingest.py first")

    manifest = build_manifest(records)
    # Both matching hashes and an existing index are required to skip GPU work.
    if manifest == read_manifest() and cfg.INDEX_DIR.exists():
        print(f"====  {len(records)} chunks already embedded and unchanged, nothing to do")
        return

    n_unique = len(set(manifest.values()))
    print(f"embedding {len(records)} chunks ({n_unique} unique) "
          f"on {cfg.EMBED_DEVICE} with {cfg.EMBED_MODEL}")

    store = VectorStore.build(records)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    store.save()
    write_manifest(manifest)

    rel = cfg.INDEX_DIR.relative_to(cfg.ROOT)
    print(f"====  wrote FAISS index ({len(records)} vectors) to {rel}")

    # Smoke-test that the persisted index answers, not merely that it built.
    hits = store.search("What does AWVALID indicate on the AXI write address channel?", k=3)
    print("====  sample query -> nearest sections:")
    for doc, score in hits:
        print(f"  {score:.3f}  {doc.metadata['context_header']}")


if __name__ == "__main__":
    main()
