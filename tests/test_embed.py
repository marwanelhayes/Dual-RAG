"""Behaviour of the spec vector index: what is encoded and what is stored."""

import pytest

import embed


class CountingEmbedder:
    """Deterministic stand-in encoder that records every passage it encodes."""

    def __init__(self):
        self.encoded = []

    def embed_documents(self, texts):
        self.encoded.extend(texts)
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


def _record(chunk_id, text="same body"):
    return {"chunk_id": chunk_id, "context_header": "Spec > 1 Intro", "text": text}


def test_every_chunk_is_encoded_even_when_texts_repeat():
    embedder = CountingEmbedder()

    embed.VectorStore.build([_record("a"), _record("b")], embedder=embedder)

    assert embedder.encoded == ["Spec > 1 Intro\n\nsame body"] * 2


def test_document_metadata_is_the_record_without_its_text():
    document = embed.record_to_document(_record("a"))

    assert document.page_content == "Spec > 1 Intro\n\nsame body"
    assert document.metadata == {"chunk_id": "a", "context_header": "Spec > 1 Intro"}


def test_main_embeds_on_every_run_and_writes_only_configured_paths(monkeypatch, tmp_path):
    """build.py --clean always rebuilds, so embed.main keeps no skip-if-unchanged path."""
    import configure as cfg

    builds, saved = [], []

    class FakeStore:
        def save(self, index_dir):
            saved.append(index_dir)
            index_dir.mkdir(exist_ok=True)

    def fake_build(records):
        builds.append(records)
        return FakeStore()

    monkeypatch.setattr(cfg, "ROOT", tmp_path)
    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cfg, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(cfg, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(embed, "load_chunks", lambda: [_record("a")])
    monkeypatch.setattr(embed.VectorStore, "build", staticmethod(fake_build))

    embed.main()
    embed.main()

    assert len(builds) == 2
    assert saved == [tmp_path / "index"] * 2
    assert embed.read_manifest(tmp_path / "manifest.json") == embed.build_manifest(builds[0])


def test_embedder_always_normalizes_passages_and_queries(monkeypatch):
    """BGE is trained for cosine similarity; vectors are L2-normalised unconditionally."""
    import sys
    import types

    import numpy as np

    calls = []

    class FakeModel:
        def __init__(self, name, device):
            pass

        def encode(self, texts, **kwargs):
            calls.append(kwargs["normalize_embeddings"])
            return np.zeros((len(texts), 2)) if isinstance(texts, list) else np.zeros(2)

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        types.SimpleNamespace(SentenceTransformer=FakeModel))
    embedder = embed.Embedder(device="cpu")

    embedder.embed_documents(["passage"])
    embedder.embed_query("query")

    assert calls == [True, True]


class TopicEmbedder:
    """Stand-in encoder mapping text about alpha and beta onto orthogonal axes."""

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0] if "alpha" in text else [0.0, 1.0]


def test_saved_index_holds_no_metadata_and_hits_are_hydrated_from_records(tmp_path):
    """Metadata lives only in the chunk records; the index stores ids, vectors, and text."""
    records = [{**_record("a", "alpha body"), "section_path": ["1 Intro"]},
               {**_record("b", "beta body"), "section_path": ["2 Next"]}]
    store = embed.VectorStore.build(records, embedder=TopicEmbedder())
    store.save(tmp_path / "index")

    loaded = embed.VectorStore.load(tmp_path / "index", records, embedder=TopicEmbedder())
    (document, score), _ = loaded.search("alpha?", k=2)

    assert document.metadata == {"chunk_id": "a", "context_header": "Spec > 1 Intro",
                                 "section_path": ["1 Intro"]}
    assert document.page_content == "Spec > 1 Intro\n\nalpha body"
    assert score == pytest.approx(1.0)
    stored = loaded._collection.get(include=["metadatas", "documents"])
    assert stored["metadatas"] == [None, None]
    assert sorted(stored["documents"]) == ["Spec > 1 Intro\n\nalpha body",
                                           "Spec > 1 Intro\n\nbeta body"]


def test_manifest_detects_missing_fresh_and_changed_surfaces(tmp_path):
    records = [_record("a", "first body"), _record("b", "second body")]
    index_dir = tmp_path / "index"
    manifest_path = tmp_path / "manifest.json"
    index_dir.mkdir()

    assert embed.index_manifest_status(
        records, index_dir, manifest_path)["reason"] == "manifest missing"
    embed.write_manifest(embed.build_manifest(records), manifest_path)
    assert embed.index_manifest_status(records, index_dir, manifest_path)["fresh"] is True
    changed = [{**records[0], "text": records[0]["text"] + " changed"}, *records[1:]]
    status = embed.index_manifest_status(changed, index_dir, manifest_path)
    assert status["fresh"] is False
    assert status["changed_ids"] == ["a"]
