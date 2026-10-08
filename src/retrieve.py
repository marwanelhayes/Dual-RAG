"""Run exact-first hybrid retrieval over one domain-local vector index."""

import re
from itertools import zip_longest

import configure as cfg
from embed import Embedder, VectorStore, available_device, record_to_document
from naming import identifier_tokens

from langchain_core.documents import Document


# Dots join hardware names (FADD.S, A2.2) but never end a token, so "signal." is "signal".
TOKEN_RE = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*")
_IDENTIFIER = re.compile(
    r"0[xX][0-9A-Fa-f]+|[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z0-9_$]+)*"
)
# RTL records too long for one window continue in ``<chunk_id>:wN`` windows.
_WINDOW = re.compile(r":w\d+$")


def tokenize(text):
    """Return lowercase tokens suitable for BM25 ranking.

    Args:
        text: Raw text to tokenize.

    Returns:
        Lowercase alphanumeric and hardware-identifier tokens.
    """
    return TOKEN_RE.findall((text or "").lower())


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
    """Rerank the union of lexical and dense candidates with a cross-encoder."""

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
    def load(cls, records, index_dir=cfg.INDEX_DIR, embedder=None, reranker=None):
        """Load a complete hybrid retriever for one domain-local corpus.

        Args:
            records: Domain records the index was built from.
            index_dir: Directory containing the matching Chroma index.
            embedder: Optional shared embedding adapter.
            reranker: Optional cross-encoder adapter.

        Returns:
            A fully assembled retriever.
        """
        documents = {record["chunk_id"]: record_to_document(record) for record in records}
        embedder = embedder or Embedder()
        store = VectorStore.load(index_dir, records, embedder=embedder)
        return cls(documents, BM25Index.build(documents), store, reranker or Reranker())

    def candidates(self, query, n=cfg.RETRIEVE_CANDIDATES):
        """Return the lexical then dense candidate union.

        Args:
            query: Query used by lexical and dense ranking.
            n: Candidate count requested from each lane.

        Returns:
            Stable candidate identifiers, lexical first.
        """
        lexical = self.bm25.search(query, n)
        dense = [document.metadata["chunk_id"]
                 for document, _ in self.store.search(query, k=n)]
        # The reranker scores every candidate, so the lanes only need a union.
        return list(dict.fromkeys(lexical + dense))

    def search(self, query, top_k=cfg.RETRIEVE_TOP_K):
        """Rank every candidate with the cross-encoder and keep the best.

        Args:
            query: Query used by every retrieval lane.
            top_k: Maximum final result count.

        Returns:
            Document and cross-encoder score pairs.
        """
        ranked = self.reranker.rank(
            query, [self.documents[item] for item in self.candidates(query)])
        return ranked[:top_k]


def _term_strength(query_terms, candidates):
    """Score a candidate name the query spells exactly as the RTL states it.

    Args:
        query_terms: Identifier-like terms taken from the query.
        candidates: Candidate names this record can be addressed by.

    Returns:
        ``0`` for an exact match, else None.
    """
    return 0 if set(query_terms).intersection(map(str, filter(None, candidates))) else None


def _phrase_strength(query_tokens, normalized_name):
    """Score a multi-token module name spelled out verbatim inside the query.

    Args:
        query_tokens: Normalized tokens of the whole query.
        normalized_name: Space-joined normalized module name.

    Returns:
        ``1`` when the name appears as a contiguous token run, else None.
    """
    tokens = str(normalized_name or "").split()
    if len(tokens) < 2:
        return None
    span = len(tokens)
    return 1 if any(query_tokens[start:start + span] == tokens
                    for start in range(len(query_tokens) - span + 1)) else None


def exact_lookup(query, records, k=None):
    """Resolve module/function names and module-owned signals before dense search.

    Args:
        query: Raw retrieval question.
        records: Module-first RTL retrieval records.
        k: Optional maximum number of documents to return.

    Returns:
        LangChain documents ordered by match strength, signal hits annotated.
    """
    query_terms = _IDENTIFIER.findall(query)
    query_tokens = identifier_tokens(query)
    # Exact hits are pinned first, so plain words colliding with a signal name cannot earn one.
    signal_terms = [term for term in query_terms
                    if term != term.casefold() or not term.isalpha()]
    found = []
    for position, record in enumerate(records):
        strength = _term_strength(query_terms, record.get("lookup_terms", []))
        if strength is None:
            strength = _phrase_strength(query_tokens, record.get("normalized_name"))
        # A record the query names by name outranks one that only shares a signal name.
        named = strength is not None
        matched_signal = None
        if record.get("source_type") == "module":
            signals = sorted(set(signal_terms).intersection(record.get("signal_names", [])),
                             key=str.casefold)
            if signals:
                matched_signal, strength = signals[0], 0
        if strength is None:
            continue
        type_order = 0 if matched_signal else 1 if record.get("source_type") == "module" else 2
        found.append((strength, not named, type_order, record.get("record_id", ""),
                      position, record, matched_signal))
    found.sort(key=lambda item: item[:5])
    documents = []
    for _, _, _, _, _, record, matched_signal in found:
        document = record_to_document(record)
        if matched_signal:
            document.metadata["exact_match_type"] = "signal"
            document.metadata["symbol_name"] = matched_signal
        documents.append(document)
    return documents[:k] if k is not None else documents


def _fold_window(document):
    """Report a continuation-window hit as a hit on the record it continues.

    Args:
        document: Retrieved document, possibly a ``<chunk_id>:wN`` window.

    Returns:
        The document, its ``chunk_id`` set to the record's id when it was a window; the
        matched window text is kept.
    """
    chunk_id = document.metadata.get("chunk_id") or ""
    base = _WINDOW.sub("", chunk_id)
    if base == chunk_id:
        return document
    return Document(page_content=document.page_content,
                    metadata={**document.metadata, "chunk_id": base})


class RTLRetriever:
    """Keep compiler-metadata exact lookup ahead of hybrid semantic retrieval."""

    def __init__(self, records, hybrid=None):
        """Create a lazy RTL retriever.

        Args:
            records: Module-first RTL retrieval records.
            hybrid: Optional generic hybrid retriever adapter.

        Returns:
            None.
        """
        self.records = list(records)
        self.hybrid = hybrid

    def _hybrid(self):
        """Load BM25, Chroma, and cross-encoder adapters only when exact lookup is short.

        Returns:
            The shared hybrid retriever, constructed on first use.
        """
        if self.hybrid is None:
            self.hybrid = Retriever.load(
                records=self.records, index_dir=cfg.RTL_INDEX_DIR)
        return self.hybrid

    def search(self, query, top_k=cfg.RETRIEVE_TOP_K):
        """Return exact module/signal hits, then hybrid-reranked module/block rows.

        Args:
            query: RTL retrieval question.
            top_k: Maximum result count.

        Returns:
            Document and ``None`` score pairs in final rank order.
        """
        wanted = top_k
        exact = exact_lookup(query, self.records)
        signal_exact = [document for document in exact
                        if document.metadata.get("exact_match_type") == "signal"]
        structures = []
        if signal_exact:
            modules = {document.metadata.get("module") for document in signal_exact}
            for record in self.records:
                if (record.get("source_type") != "construct"
                        or record.get("module") not in modules):
                    continue
                names = {str(name).casefold() for name in [
                    *record.get("signals_read", []),
                    *record.get("signals_written", []),
                ]}
                if any(str(document.metadata.get("symbol_name", "")).casefold()
                       in names for document in signal_exact):
                    structures.append(record_to_document(record))
        if structures:
            signal_names = {str(document.metadata.get("symbol_name", "")).casefold()
                            for document in signal_exact}
            structures.sort(key=lambda document: (
                not bool(signal_names.intersection(
                    str(name).casefold()
                    for name in document.metadata.get("signals_written", []))),
                document.metadata.get("module", ""),
                document.metadata.get("start_line", 0),
            ))
        # Exact hits and the blocks using their signals alternate, so neither fills the budget.
        prefix, seen = [], set()
        for document in (item for pair in zip_longest(exact, structures) for item in pair):
            chunk_id = document and document.metadata.get("chunk_id")
            if chunk_id and chunk_id not in seen:
                prefix.append(document)
                seen.add(chunk_id)
        if len(prefix) >= wanted:
            return [(document, None) for document in prefix[:wanted]]
        hybrid = self._hybrid()
        ranked = hybrid.search(query, top_k=wanted + len(prefix))
        output = list(prefix)
        prefix_ids = set(seen)
        for hit in ranked:
            document = _fold_window(hit[0] if isinstance(hit, tuple) else hit)
            chunk_id = document.metadata.get("chunk_id")
            if chunk_id in seen:
                continue
            output.append(document)
            seen.add(chunk_id)
            if len(output) >= wanted:
                break
        modules = {document.metadata.get("module") for document in output
                   if document.metadata.get("source_type") == "module"}
        owned = {}
        for record in self.records:
            document = _fold_window(record_to_document(record))
            if (record.get("source_type") == "construct"
                    and record.get("module") in modules
                    and document.metadata["chunk_id"] not in seen):
                owned.setdefault(document.metadata["chunk_id"], document)
        owned = list(owned.values())
        if owned and getattr(hybrid, "reranker", None):
            reranked = [document for document, _ in hybrid.reranker.rank(
                query, [*output, *owned])]
            pinned = [document for document in reranked
                      if document.metadata.get("chunk_id") in prefix_ids][
                          :max(1, wanted // 2)]
            pinned_ids = {document.metadata.get("chunk_id") for document in pinned}
            output = [*pinned, *(document for document in reranked
                                 if document.metadata.get("chunk_id") not in pinned_ids)]
        return [(document, None) for document in output[:wanted]]
