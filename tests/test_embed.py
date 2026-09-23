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
