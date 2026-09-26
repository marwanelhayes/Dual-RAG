"""Run exact-first hybrid retrieval over one domain-local vector index."""

import re

import configure as cfg
from embed import Embedder, VectorStore, available_device, record_to_document
from naming import identifier_tokens, normalize_identifier

from langchain_core.documents import Document


TOKEN_RE = re.compile(r"[a-z0-9_.]+")
IDENTIFIER_RE = re.compile(
    r"\b(?:0x[0-9a-f]+|[A-Z][A-Z0-9_.]{2,}|"
    r"[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_.]+|"
    r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]+)\b")
# These describe the question's domain, not a specific hardware identity.
CONTEXT_TERMS = frozenset({"RTL", "SPEC", "AXI", "FPU"})
_IDENTIFIER = re.compile(
    r"0[xX][0-9A-Fa-f]+|[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z0-9_$]+)*"
)
# RTL records too long for one window continue in ``<chunk_id>:wN`` windows.
_WINDOW = re.compile(r":w\d+$")
_QUERY_STOP_WORDS = frozenset({
    "the", "and", "for", "from", "into", "what", "where", "which", "who",
    "does", "how", "this", "that", "with", "begin", "end", "else", "case",
    "module", "input", "output", "wire", "logic", "assign", "always", "initial",
    "function", "task", "return", "when", "then", "transfer",
})


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


def _query_terms(query):
    """Extract sufficiently distinctive identifier-like query terms.

    Args:
        query: Raw retrieval question.

    Returns:
        Identifier-shaped terms, excluding short words and RTL stop words.
    """
    return [term for term in _IDENTIFIER.findall(query)
            if (term.lower().startswith("0x") or len(term) >= 3)
            and term.casefold() not in _QUERY_STOP_WORDS]


def _term_strength(query_terms, candidates):
    """Score exact, case-folded, then normalized candidate-name matches.

    Args:
        query_terms: Identifier-like terms taken from the query.
        candidates: Candidate names this record can be addressed by.

    Returns:
        ``0``, ``1``, or ``2`` for decreasing match strength, else None.
    """
    exact = set(query_terms)
    folded = {term.casefold() for term in query_terms}
    normalized = {normalize_identifier(term) for term in query_terms}
    candidates = [str(candidate) for candidate in candidates if candidate]
    if exact.intersection(candidates):
        return 0
    if folded.intersection(candidate.casefold() for candidate in candidates):
        return 1
    if normalized.intersection(normalize_identifier(candidate) for candidate in candidates):
        return 2
    return None


def _phrase_strength(query_tokens, normalized_name):
    """Score a multi-token module name spelled out verbatim inside the query.

    Args:
        query_tokens: Normalized tokens of the whole query.
        normalized_name: Space-joined normalized module name.

    Returns:
        ``3`` when the name appears as a contiguous token run, else None.
    """
    tokens = str(normalized_name or "").split()
    if len(tokens) < 2:
        return None
    span = len(tokens)
    return 3 if any(query_tokens[start:start + span] == tokens
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
    query_terms = _query_terms(query)
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
            signal_matches = []
            for signal in record.get("signal_metadata", []):
                signal_strength = _term_strength(
                    signal_terms,
                    [signal.get("original_name"), signal.get("normalized_name")])
                if signal_strength is not None:
                    signal_matches.append((signal_strength, signal))
            if signal_matches:
                signal_matches.sort(key=lambda item: (
                    item[0], item[1].get("original_name", "").casefold(),
                    item[1].get("signal_id", "")))
                signal_strength, matched_signal = signal_matches[0]
                if strength is None or signal_strength < strength:
                    strength = signal_strength
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
            document.metadata["symbol_name"] = matched_signal.get("original_name")
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

    def search(self, query, top_k=cfg.AGENT_SEARCH_K):
        """Return exact module/signal hits, then hybrid-reranked module/block rows.

        Args:
            query: RTL retrieval question.
            top_k: Maximum result count.

        Returns:
            LangChain documents in final rank order.
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
        if len(exact) >= wanted:
            if structures:
                structure_count = 0 if wanted == 1 else min(len(structures), wanted // 2)
                selected = signal_exact[:wanted - structure_count]
                selected_structures = structures[:structure_count]
                output = [item for pair in zip(selected, selected_structures)
                          for item in pair]
                output.extend(selected[len(selected_structures):])
                used = {document.metadata.get("chunk_id") for document in output}
                output.extend(document for document in exact
                              if document.metadata.get("chunk_id") not in used)
                if output:
                    return output[:wanted]
            return exact[:wanted]
        if structures:
            exact_ids = {document.metadata.get("chunk_id") for document in exact}
            output = [*exact, *(document for document in structures
                                if document.metadata.get("chunk_id") not in exact_ids)]
            if len(output) >= wanted:
                return output[:wanted]
        hybrid = self._hybrid()
        ranked = hybrid.search(query, top_k=wanted + len(exact))
        output = list(exact)
        seen = {document.metadata.get("chunk_id") for document in exact}
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
            exact_ids = {document.metadata.get("chunk_id") for document in exact}
            pinned = [document for document in reranked
                      if document.metadata.get("chunk_id") in exact_ids][
                          :max(1, wanted // 2)]
            pinned_ids = {document.metadata.get("chunk_id") for document in pinned}
            output = [*pinned, *(document for document in reranked
                                 if document.metadata.get("chunk_id") not in pinned_ids)]
        return output[:wanted]
