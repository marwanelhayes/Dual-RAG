"""Hermetic contracts for exact-first domain-local hybrid retrieval."""

from langchain_core.documents import Document

import retrieve


def _doc(chunk_id, text):
    return Document(page_content=text, metadata={"chunk_id": chunk_id})


class _StaticRanker:
    def __init__(self, ranked):
        self.ranked = ranked

    def search(self, query, k):
        return self.ranked[:k]


class _Dense:
    def __init__(self, documents):
        self.documents = documents

    def search(self, query, k):
        return [(document, 0.9) for document in self.documents[:k]]


class _ReverseReranker:
    def rank(self, query, documents):
        return [(document, float(index)) for index, document in
                enumerate(reversed(documents), start=1)]


def _role_doc(chunk_id, scores):
    return Document(page_content=chunk_id,
                    metadata={"chunk_id": chunk_id, "document_role_scores": scores})


NORMATIVE_SCORES = {"normative": 0.8, "architecture": 0.1, "explanatory": 0.1}
EXPLANATORY_SCORES = {"normative": 0.3, "architecture": 0.0, "explanatory": 0.7}


def test_authority_ranks_a_defining_source_above_an_explaining_one():
    assert (retrieve.document_authority(_role_doc("a", NORMATIVE_SCORES))
            > retrieve.document_authority(_role_doc("b", EXPLANATORY_SCORES)))


def test_candidates_without_role_scores_have_no_authority():
    assert retrieve.document_authority(_doc("rtl:fpu_decoder", "always_comb")) == 0.0


def test_authority_breaks_a_tie_toward_the_defining_source():
    documents = [_role_doc("explains", EXPLANATORY_SCORES),
                 _role_doc("defines", NORMATIVE_SCORES)]

    adjusted = retrieve.apply_authority(documents, [1.0, 1.0], weight=1.0)

    assert adjusted[1] > adjusted[0]
    assert round(sum(adjusted), 6) == 2.0


def test_a_single_source_shortlist_is_left_unshifted():
    documents = [_role_doc("one", NORMATIVE_SCORES), _role_doc("two", NORMATIVE_SCORES)]

    assert retrieve.apply_authority(documents, [2.0, 1.0], weight=5.0) == [2.0, 1.0]


def test_rtl_candidates_are_ranked_without_any_authority_shift():
    documents = [_doc("rtl:a", "one"), _doc("rtl:b", "two")]

    assert retrieve.apply_authority(documents, [2.0, 1.0], weight=5.0) == [2.0, 1.0]


def test_tokenizer_keeps_hardware_punctuation_inside_tokens():
    assert retrieve.tokenize("FADD.S drives result_o") == ["fadd.s", "drives", "result_o"]


def test_graph_store_and_graph_sentence_helpers_are_gone():
    assert not hasattr(retrieve, "GraphStore")
    assert not hasattr(retrieve, "node_sentence")
    assert not hasattr(retrieve, "edge_sentence")


def test_dead_retrieval_entry_points_are_gone():
    assert not hasattr(retrieve, "search")
    assert not hasattr(retrieve, "load_records")
    assert not hasattr(retrieve, "main")


def test_candidates_are_the_lexical_then_dense_union_for_the_reranker():
    documents = {key: _doc(key, key) for key in ("a", "b", "c")}
    retriever = retrieve.Retriever(documents, _StaticRanker(["a", "b"]),
                                   _Dense([documents["c"], documents["a"]]),
                                   _ReverseReranker())

    assert retriever.candidates("write address", n=2) == ["a", "b", "c"]


def test_reranker_decides_even_when_the_question_names_an_identifier():
    literal = _doc("literal", "AWVALID declaration")
    semantic = _doc("semantic", "The sender holds the address stable until acceptance.")
    documents = {"literal": literal, "semantic": semantic}

    class Relevance:
        def rank(self, query, documents):
            return sorted([(doc, 10.0 if doc is semantic else 1.0) for doc in documents],
                          key=lambda pair: pair[1], reverse=True)

    retriever = retrieve.Retriever(documents, _StaticRanker(["literal"]),
                                   _Dense([semantic]), Relevance())

    hits = retriever.search("What constrains AWVALID?", top_k=1)

    assert [doc.metadata["chunk_id"] for doc, _ in hits] == ["semantic"]


def test_every_retriever_shares_one_loaded_reranker(monkeypatch):
    loaded = []

    class OneModel:
        def __init__(self):
            loaded.append(self)

    monkeypatch.setattr(retrieve, "Reranker", OneModel)
    retrieve.shared_reranker.cache_clear()

    assert retrieve.shared_reranker() is retrieve.shared_reranker()
    assert len(loaded) == 1
    retrieve.shared_reranker.cache_clear()


def test_the_reranker_runs_in_half_precision_on_the_gpu(monkeypatch):
    import sentence_transformers

    class FakeCrossEncoder:
        def __init__(self, model, device):
            self.halved = False

        def half(self):
            self.halved = True
            return self

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", FakeCrossEncoder)

    assert retrieve.Reranker(model="m", device="cuda").model.halved == \
        retrieve.available_device("cuda").startswith("cuda")
    assert not retrieve.Reranker(model="m", device="cpu").model.halved


def test_a_retriever_without_a_vector_store_ranks_lexical_candidates_only():
    documents = {key: _doc(key, key) for key in ("a", "b", "c")}
    retriever = retrieve.Retriever(documents, _StaticRanker(["b", "a"]), None,
                                   _ReverseReranker())

    assert retriever.candidates("anything", n=2) == ["b", "a"]


def test_loading_without_an_index_loads_no_embedder(monkeypatch):
    def no_embedder(*args, **kwargs):
        raise AssertionError("embedder loaded")

    monkeypatch.setattr(retrieve, "Embedder", no_embedder)
    records = [{"chunk_id": "rtl:a", "context_header": "RTL > a", "text": "module a"}]

    retriever = retrieve.Retriever.load(records, index_dir=None, reranker=_ReverseReranker())

    assert retriever.store is None


def test_the_rtl_lane_loads_without_its_vector_index(monkeypatch):
    seen = {}
    monkeypatch.setattr(retrieve.Retriever, "load",
                        classmethod(lambda cls, **kwargs: seen.update(kwargs) or "hybrid"))

    assert retrieve.RTLRetriever([])._hybrid() == "hybrid"
    assert seen["index_dir"] is None


def test_reranker_scores_are_raw_logits_whatever_the_model_head(monkeypatch):
    import torch

    seen = {}

    class SigmoidHead:
        def predict(self, pairs, batch_size, activation_fn=None):
            seen["activation"] = activation_fn
            return [0.5 for _ in pairs]

    reranker = retrieve.Reranker.__new__(retrieve.Reranker)
    reranker.batch, reranker.model = 4, SigmoidHead()

    reranker.score_pairs([("q", "d")])

    assert isinstance(seen["activation"], torch.nn.Identity)
