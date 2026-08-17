"""Hermetic contracts for exact-first domain-local hybrid retrieval."""

from langchain_core.documents import Document

import retrieve
from embed import available_device


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


class _TrackingReranker:
    def __init__(self):
        self.calls = []

    def rank(self, query, documents):
        self.calls.append([document.metadata["chunk_id"] for document in documents])
        return [(document, 1.0) for document in documents]


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


def test_model_device_preserves_cpu_and_falls_back_without_cuda():
    assert available_device("cpu", cuda_available=False) == "cpu"
    assert available_device("cuda", cuda_available=True) == "cuda"
    assert available_device("cuda:1", cuda_available=False) == "cpu"


def test_tokenizer_keeps_hardware_punctuation_inside_tokens():
    assert retrieve.tokenize("FADD.S drives result_o") == ["fadd.s", "drives", "result_o"]


def test_identifier_terms_find_hardware_tokens_without_plain_prose():
    assert retrieve.identifier_terms("Does AWVALID drive FPUValid_D at 0x300?") == [
        "AWVALID", "FPUValid_D", "0x300",
    ]
    assert retrieve.identifier_terms("what does the design require") == []


def test_exact_document_lookup_is_token_bounded_and_case_insensitive():
    documents = {
        "longer": _doc("longer", "MAWVALID is unrelated"),
        "literal": _doc("literal", "The source asserts awvalid."),
    }

    assert retrieve.exact_document_ids(documents, ["AWVALID"]) == ["literal"]


def test_reciprocal_rank_fusion_combines_lanes_without_raw_score_comparison():
    fused = retrieve.reciprocal_rank_fusion([["a", "b"], ["b", "c"]], rrf_k=10)

    assert fused[0] == "b" and set(fused) == {"a", "b", "c"}


def test_exact_identifier_hit_stays_ahead_of_semantic_candidates():
    exact = _doc("exact", "AXI rule: AWVALID remains asserted.")
    semantic = _doc("semantic", "A fluent description of write-address handshakes.")
    documents = {document.metadata["chunk_id"]: document
                 for document in (exact, semantic)}
    retriever = retrieve.Retriever(
        documents, _StaticRanker(["semantic"]), _Dense([semantic]),
        _ReverseReranker())

    hits = retriever.search("What constrains AWVALID?", top_k=1)

    assert hits[0][0].metadata["chunk_id"] == "exact"


def test_exact_hits_filling_result_skip_remaining_reranker_work():
    exact = _doc("exact", "AXI rule: AWVALID remains asserted.")
    semantic = _doc("semantic", "Write-address handshake prose.")
    documents = {document.metadata["chunk_id"]: document
                 for document in (exact, semantic)}
    reranker = _TrackingReranker()
    retriever = retrieve.Retriever(
        documents, _StaticRanker(["semantic"]), _Dense([semantic]), reranker)

    retriever.search("What constrains AWVALID?", top_k=1)

    assert reranker.calls == [["exact"]]


def test_callers_can_pin_known_lowercase_identifiers_into_exact_lane():
    exact = _doc("mstatus", "The mstatus register controls machine status.")
    semantic = _doc("near", "General privileged architecture prose.")
    documents = {document.metadata["chunk_id"]: document
                 for document in (exact, semantic)}
    retriever = retrieve.Retriever(
        documents, _StaticRanker(["near"]), _Dense([semantic]),
        _ReverseReranker())

    hits = retriever.search("machine status", top_k=1, exact_terms=["mstatus"])

    assert hits[0][0].metadata["chunk_id"] == "mstatus"


def test_candidate_order_deduplicates_dense_hits_already_exact():
    exact = _doc("exact", "AWVALID")
    other = _doc("other", "write address")
    documents = {"exact": exact, "other": other}
    retriever = retrieve.Retriever(
        documents, _StaticRanker(["exact", "other"]), _Dense([exact, other]),
        _ReverseReranker())

    candidates = retriever.candidates("AWVALID", n=2)

    assert candidates == ["exact", "other"]


def test_graph_store_and_graph_sentence_helpers_are_gone():
    assert not hasattr(retrieve, "GraphStore")
    assert not hasattr(retrieve, "node_sentence")
    assert not hasattr(retrieve, "edge_sentence")
