"""Run exact-first hybrid retrieval over one domain-local vector index."""

import re

import configure as cfg
from embed import Embedder, VectorStore, available_device, record_to_document
from ingest import load_chunks


TOKEN_RE = re.compile(r"[a-z0-9_.]+")
IDENTIFIER_RE = re.compile(
    r"\b(?:0x[0-9a-f]+|[A-Z][A-Z0-9_.]{2,}|"
    r"[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_.]+|"
    r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]+)\b")
# These describe the question's domain, not a specific hardware identity.
CONTEXT_TERMS = frozenset({"RTL", "SPEC", "AXI", "FPU"})


def tokenize(text):
    """Return lowercase tokens suitable for BM25 ranking.

    Args:
        text: Raw text to tokenize.

    Returns:
        Lowercase alphanumeric and hardware-identifier tokens.
    """
    return TOKEN_RE.findall((text or "").lower())


def identifier_terms(text):
    """Return unique identifier-shaped query terms in encounter order.

    Args:
        text: Query text to scan.

    Returns:
        Exact-looking hardware identifiers.
    """
    return [term for term in dict.fromkeys(IDENTIFIER_RE.findall(text or ""))
            if term not in CONTEXT_TERMS]


def exact_document_ids(documents, terms):
    """Prioritize the most selective matching identifier, retaining frequency ties.

    Args:
        documents: LangChain documents keyed by stable chunk identifier.
        terms: Literal identifier terms to match.

    Returns:
        Matching IDs in corpus order. Broad-only hits remain eligible via hybrid search.
    """
    wanted = [term for term in dict.fromkeys(terms or []) if term]
    if not wanted:
        return []
    patterns = [
        re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])",
                   re.IGNORECASE)
        for term in wanted
    ]
    matches = [{chunk_id for chunk_id, document in documents.items()
                if pattern.search(document.page_content)} for pattern in patterns]
    matches = [ids for ids in matches if ids]
    if not matches:
        return []
    smallest = min(map(len, matches))
    selected = set().union(*(ids for ids in matches if len(ids) == smallest))
    return [chunk_id for chunk_id in documents if chunk_id in selected]


class BM25Index:
    """Provide in-memory lexical ranking over one domain's records."""

    def __init__(self, chunk_ids, corpus_tokens):
        """Build an index over aligned chunk identifiers and token lists.

        Args:
            chunk_ids: Stable identifiers aligned with the tokenized corpus.
            corpus_tokens: Token lists for each record.

        Returns:
            None.
        """
        from rank_bm25 import BM25Okapi

        self.chunk_ids = list(chunk_ids)
        self._bm25 = BM25Okapi(corpus_tokens)

    @classmethod
    def build(cls, documents):
        """Tokenize and index supplied LangChain documents.

        Args:
            documents: Documents keyed by stable chunk identifier.

        Returns:
            A lexical index over those documents.
        """
        chunk_ids = list(documents)
        corpus = [tokenize(documents[chunk_id].page_content) for chunk_id in chunk_ids]
        return cls(chunk_ids, corpus)

    def search(self, query, k):
        """Return the highest-ranked chunk identifiers for a query.

        Args:
            query: Text query to rank.
            k: Maximum number of identifiers to return.

        Returns:
            Identifiers ordered by decreasing BM25 score.
        """
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
        return [self.chunk_ids[index] for index in ranked[:k]]


def reciprocal_rank_fusion(rankings, rrf_k=cfg.RETRIEVE_RRF_K):
    """Fuse ranked identifier lists without comparing incompatible scores.

    Args:
        rankings: Ranked identifier sequences to combine.
        rrf_k: Damping constant applied to every rank contribution.

    Returns:
        Identifiers ordered by decreasing fused score.
    """
    scores = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)


def document_authority(document):
    """Score how far one candidate's source document defines rather than explains.

    Args:
        document: Candidate LangChain document.

    Returns:
        Authority in ``[0, 1]``; zero when the source carries no role scores.
    """
    scores = (document.metadata or {}).get("document_role_scores") or {}
    return sum(cfg.ROLE_AUTHORITY.get(role, 0.0) * share for role, share in scores.items())


def apply_authority(documents, scores, weight=None):
    """Shift relevance scores by each candidate's authority within this shortlist.

    Args:
        documents: Candidate documents aligned with ``scores``.
        scores: Cross-encoder relevance logits.
        weight: Optional override for the configured authority weight.

    Returns:
        Adjusted scores in input order.
    """
    weight = cfg.RERANK_AUTHORITY_WEIGHT if weight is None else weight
    authorities = [document_authority(document) for document in documents]
    if not authorities or not weight:
        return list(scores)
    # Centering keeps a single-source corpus unshifted and RTL candidates untouched.
    mean = sum(authorities) / len(authorities)
    return [score + weight * (authority - mean)
            for score, authority in zip(scores, authorities)]


class Reranker:
    """Rank a bounded candidate set with a query-document cross-encoder."""

    def __init__(self, model=cfg.RERANK_MODEL, device=cfg.RERANK_DEVICE,
                 batch=cfg.RERANK_BATCH):
        """Load the configured cross-encoder lazily at adapter construction.

        Args:
            model: Cross-encoder model name or local path.
            device: Requested inference device.
            batch: Prediction batch size.

        Returns:
            None.
        """
        from sentence_transformers import CrossEncoder

        self.batch = batch
        self.device = available_device(device)
        self.model = CrossEncoder(model, device=self.device)

    def score_pairs(self, pairs):
        """Score bounded text pairs for bridge or retrieval reranking.

        Args:
            pairs: Sequence of two-string tuples.

        Returns:
            Floating-point cross-encoder logits in input order.
        """
        pairs = list(pairs)
        if not pairs:
            return []
        scores = self.model.predict(pairs, batch_size=self.batch)
        return [float(score) for score in scores]

    def rank(self, query, documents, authority_weight=None):
        """Return candidates ordered by cross-encoder relevance adjusted for authority.

        Args:
            query: Query paired with every candidate.
            documents: Candidate LangChain documents.
            authority_weight: Optional override for the configured authority weight.

        Returns:
            Document and adjusted-score pairs in descending order.
        """
        documents = list(documents)
        scores = self.score_pairs((query, document.page_content) for document in documents)
        adjusted = apply_authority(documents, scores, weight=authority_weight)
        return sorted(zip(documents, adjusted), key=lambda pair: pair[1], reverse=True)


class Retriever:
    """Combine exact, lexical, dense, and cross-encoder retrieval lanes."""

    def __init__(self, documents, bm25, store, reranker):
        """Assemble independently replaceable ranking adapters at one seam.

        Args:
            documents: Documents keyed by stable chunk identifier.
            bm25: Lexical ranking adapter.
            store: Dense vector-store adapter.
            reranker: Cross-encoder ranking adapter.

        Returns:
            None.
        """
        self.documents = documents
        self.bm25 = bm25
        self.store = store
        self.reranker = reranker

    @classmethod
    def load(cls, records=None, index_dir=cfg.INDEX_DIR, embedder=None,
             reranker=None):
        """Load a complete hybrid retriever for one domain-local corpus.

        Args:
            records: Optional records; defaults to specification chunks.
            index_dir: Directory containing the matching FAISS index.
            embedder: Optional shared embedding adapter.
            reranker: Optional cross-encoder adapter.

        Returns:
            A fully assembled retriever.
        """
        records = load_chunks() if records is None else records
        documents = {record["chunk_id"]: record_to_document(record) for record in records}
        embedder = embedder or Embedder()
        store = VectorStore.load(index_dir=index_dir, embedder=embedder)
        return cls(documents, BM25Index.build(documents), store, reranker or Reranker())

    def candidates(self, query, n=cfg.RETRIEVE_CANDIDATES, exact_terms=None):
        """Return exact hits followed by fused lexical and dense candidates.

        Args:
            query: Query used by lexical and dense ranking.
            n: Candidate count requested from each broad lane.
            exact_terms: Optional explicit literal terms.

        Returns:
            Stable candidate identifiers in ranking order.
        """
        terms = identifier_terms(query) if exact_terms is None else exact_terms
        exact = exact_document_ids(self.documents, terms)
        lexical = self.bm25.search(query, n)
        dense = [document.metadata["chunk_id"]
                 for document, _ in self.store.search(query, k=n)]
        fused = reciprocal_rank_fusion([lexical, dense])
        exact_set = set(exact)
        return exact + [chunk_id for chunk_id in fused if chunk_id not in exact_set]

    def search(self, query, top_k=cfg.RETRIEVE_TOP_K, exact_terms=None):
        """Keep a bounded exact prefix, then fill by relevance across all candidates.

        Args:
            query: Query used by every retrieval lane.
            top_k: Maximum final result count.
            exact_terms: Optional explicit literal terms.

        Returns:
            Document and cross-encoder score pairs.
        """
        if top_k <= 0:
            return []
        terms = identifier_terms(query) if exact_terms is None else exact_terms
        exact_ids = exact_document_ids(self.documents, terms)
        candidate_ids = self.candidates(query, exact_terms=terms)
        exact_set = set(exact_ids)
        if top_k == 1 and exact_ids:
            return self.reranker.rank(query, [self.documents[item] for item in exact_ids])[:1]
        ranked = self.reranker.rank(query, [self.documents[item] for item in candidate_ids
                                            if item in self.documents])
        # Exact identity gets priority, but cannot reserve the entire evidence budget.
        pinned = [pair for pair in ranked if pair[0].metadata["chunk_id"] in exact_set][
            :max(1, top_k // 2)]
        pinned_ids = {document.metadata["chunk_id"] for document, _ in pinned}
        remaining = [pair for pair in ranked if pair[0].metadata["chunk_id"] not in pinned_ids]
        return pinned + remaining[:top_k - len(pinned)]


def main():
    """Load the specification index and run one inspectable smoke query.

    Returns:
        None.
    """
    records = load_chunks()
    if not records:
        raise SystemExit(f"no chunks in {cfg.CHUNKS_PATH}; run src/ingest.py first")
    if not cfg.INDEX_DIR.exists():
        raise SystemExit(f"no index at {cfg.INDEX_DIR}; run src/embed.py first")
    retriever = Retriever.load(records)
    query = "What does AWVALID indicate on the AXI write address channel?"
    candidate_ids = retriever.candidates(query)
    hits = retriever.search(query)
    print(f"====  {len(candidate_ids)} candidates -> top {len(hits)}:")
    for document, score in hits:
        print(f"  {score:+.3f}  {document.metadata['context_header']}")


if __name__ == "__main__":
    main()
