"""Embed ingested chunks once and persist their Chroma similarity index.

Run ``python src/embed.py`` to transform ``output/chunks.jsonl`` into the Chroma index
and freshness manifest configured in ``configure.py``.
"""

import json
from pathlib import Path

import configure as cfg
from ingest import content_hash, load_chunks

from langchain_core.documents import Document

# One collection per index directory; the directory names the index.
COLLECTION = "chunks"


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


def build_manifest(records):
    """Map every chunk id to the fingerprint of its embedded text.

    Args:
        records: the chunk dicts from load_chunks.

    Returns:
        dict of chunk_id -> content hash of ``context_header`` and ``text``.
    """
    # Metadata is read from the chunk file at query time; only embedded text stales.
    return {r["chunk_id"]: content_hash(r["context_header"], r["text"]) for r in records}


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
    return Document(page_content=page_content, metadata=metadata)


class Embedder:
    """Encode passages and queries with the BGE sentence encoder."""

    def __init__(self, model=cfg.EMBED_MODEL, device=cfg.EMBED_DEVICE,
                 batch=cfg.EMBED_BATCH):
        """Load the encoder and move it onto the target device.

        Args:
            model: sentence-transformers model name.
            device: "cuda" for the GPU, "cpu" for the fallback.
            batch: chunks per forward pass.

        Returns:
            None.
        """
        # Defer the multi-gigabyte model stack until embedding is requested.
        from sentence_transformers import SentenceTransformer

        self.batch = batch
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
            normalize_embeddings=True,
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
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vec.tolist()


def _client(index_dir):
    """Open the persistent Chroma client rooted at one index directory.

    Args:
        index_dir: Directory holding the index.

    Returns:
        A ``chromadb.PersistentClient``.
    """
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(path=str(index_dir),
                                     settings=Settings(anonymized_telemetry=False))


class VectorStore:
    """Build, persist, and search a Chroma index whose hits are hydrated from records."""

    def __init__(self, documents, embedder, collection=None, vectors=None):
        """Hold the searchable documents and either a saved collection or fresh vectors.

        Args:
            documents: chunk_id -> Document, the metadata source for every hit.
            embedder: the Embedder used for queries.
            collection: a persisted Chroma collection, or None until ``save``.
            vectors: passage vectors in ``documents`` order, awaiting ``save``.

        Returns:
            None.
        """
        self.documents = documents
        self.embedder = embedder
        self._collection = collection
        self._vectors = vectors

    @classmethod
    def build(cls, records, embedder=None):
        """Embed every chunk once; the vectors are written by ``save``.

        Args:
            records: the chunk dicts from load_chunks.
            embedder: an Embedder to reuse, or None to construct the default one.

        Returns:
            a VectorStore holding the vectors in memory.
        """
        embedder = embedder or Embedder()
        documents = {r["chunk_id"]: record_to_document(r) for r in records}
        vectors = embedder.embed_documents([d.page_content for d in documents.values()])
        return cls(documents, embedder, vectors=vectors)

    def save(self, index_dir):
        """Write ids, vectors, and embedded text into a fresh collection.

        Args:
            index_dir: directory to persist the index into.

        Returns:
            None.
        """
        client = _client(index_dir)
        if COLLECTION in {c.name for c in client.list_collections()}:
            client.delete_collection(COLLECTION)
        # Cosine space: score = 1 - distance, the cosine similarity of the vectors.
        collection = client.create_collection(
            COLLECTION, configuration={"hnsw": {"space": "cosine"}}, embedding_function=None)
        ids = list(self.documents)
        texts = [self.documents[i].page_content for i in ids]
        step = client.get_max_batch_size()
        for start in range(0, len(ids), step):
            end = start + step
            collection.add(ids=ids[start:end], embeddings=self._vectors[start:end],
                           documents=texts[start:end])
        self._collection = collection

    @classmethod
    def load(cls, index_dir, records, embedder=None):
        """Open a saved index; hits are hydrated from the given records.

        Args:
            index_dir: directory a previous run saved the index into.
            records: the chunk dicts the index was built from.
            embedder: an Embedder to reuse, or None to construct the default one.

        Returns:
            a VectorStore ready to search.
        """
        embedder = embedder or Embedder()
        collection = _client(index_dir).get_collection(COLLECTION)
        return cls({r["chunk_id"]: record_to_document(r) for r in records},
                   embedder, collection)

    def search(self, query, k=5):
        """Return the k chunks nearest a query, with their cosine similarities.

        Args:
            query: the raw user question.
            k: how many neighbours to return.

        Returns:
            list of (Document, score) pairs, nearest first.
        """
        result = self._collection.query(
            query_embeddings=[self.embedder.embed_query(query)],
            n_results=min(k, self._collection.count()), include=["distances"])
        return [(self.documents[i], 1.0 - d)
                for i, d in zip(result["ids"][0], result["distances"][0])]


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


def index_manifest_status(records, index_dir, manifest_path):
    """Compare current module/structure embedding surfaces with the saved manifest.

    Args:
        records: Current retrieval records.
        index_dir: Directory expected to hold the RTL Chroma index.
        manifest_path: Path of the manifest written beside that index.

    Returns:
        Status mapping with ``fresh``, a human ``reason``, and the id deltas.
    """
    records = list(records)
    seen, duplicate_ids = set(), set()
    for record in records:
        if record["chunk_id"] in seen:
            duplicate_ids.add(record["chunk_id"])
        seen.add(record["chunk_id"])
    current = build_manifest(records)
    status = {
        "fresh": False,
        "reason": "",
        "index_dir": str(index_dir),
        "manifest_path": str(manifest_path),
        "current_count": len(current),
        "manifest_count": 0,
        "added_ids": [],
        "removed_ids": [],
        "changed_ids": [],
    }
    if not records:
        status["reason"] = "source records empty"
        return status
    if duplicate_ids:
        status.update(reason="duplicate chunk ids", duplicate_ids=sorted(duplicate_ids))
        return status
    if not Path(index_dir).is_dir():
        status["reason"] = "index missing"
        return status
    if not Path(manifest_path).is_file():
        status["reason"] = "manifest missing"
        return status
    try:
        saved = read_manifest(Path(manifest_path))
    except (OSError, ValueError, TypeError) as exc:
        status.update(reason="manifest unreadable", error=f"{type(exc).__name__}: {exc}")
        return status
    if (not isinstance(saved, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in saved.items())):
        status["reason"] = "manifest invalid"
        return status
    current_ids, saved_ids = set(current), set(saved)
    status.update(
        manifest_count=len(saved),
        added_ids=sorted(current_ids - saved_ids),
        removed_ids=sorted(saved_ids - current_ids),
        changed_ids=sorted(chunk_id for chunk_id in current_ids & saved_ids
                           if current[chunk_id] != saved[chunk_id]),
    )
    status["fresh"] = current == saved
    status["reason"] = "fresh" if status["fresh"] else "manifest mismatch"
    return status


def main():
    """Embed the corpus into Chroma and write its manifest.

    Returns:
        None.
    """
    records = load_chunks()
    if not records:
        raise SystemExit(f"no chunks in {cfg.CHUNKS_PATH}; run src/ingest.py first")

    print(f"embedding {len(records)} chunks "
          f"on {cfg.EMBED_DEVICE} with {cfg.EMBED_MODEL}")

    store = VectorStore.build(records)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    store.save(cfg.INDEX_DIR)
    write_manifest(build_manifest(records), cfg.MANIFEST_PATH)

    rel = cfg.INDEX_DIR.relative_to(cfg.ROOT)
    print(f"====  wrote Chroma index ({len(records)} vectors) to {rel}")


if __name__ == "__main__":
    main()
