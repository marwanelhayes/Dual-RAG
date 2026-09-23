"""Behaviour of the spec vector index: what is encoded and what is stored."""

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
