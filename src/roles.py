"""Score the normative and semantic role of documents and chunks by wording alone."""

import re

import configure as cfg


def _compile(vocabulary):
    """Compile one role vocabulary into word-bounded alternation patterns.

    Args:
        vocabulary: Role name to literal or regular-expression term list.

    Returns:
        Role name to a single compiled case-insensitive pattern.
    """
    return {role: re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.IGNORECASE)
            for role, terms in vocabulary.items()}


_DOCUMENT_PATTERNS = _compile(cfg.ROLE_DOCUMENT_VOCABULARY)
_CHUNK_PATTERNS = _compile(cfg.ROLE_CHUNK_VOCABULARY)


def _normalize(raw):
    """Turn weighted match counts into a distribution that sums to one.

    Args:
        raw: Role name to non-negative weighted score.

    Returns:
        Role name to share of the total, or all zeros when nothing matched.
    """
    total = sum(raw.values())
    if not total:
        return {role: 0.0 for role in raw}
    return {role: round(score / total, 4) for role, score in raw.items()}


def leading_role(scores, margin=cfg.ROLE_MARGIN):
    """Name the winning role only when its lead over the runner-up is decisive.

    Args:
        scores: Role name to normalized score.
        margin: Minimum gap between the top two scores.

    Returns:
        The winning role name, or ``None`` when the evidence is not decisive.
    """
    ranked = sorted(scores.values(), reverse=True)
    if not ranked or not ranked[0]:
        return None
    runner_up = ranked[1] if len(ranked) > 1 else 0.0
    if ranked[0] - runner_up < margin:
        return None
    return max(scores, key=scores.get)


def chunk_role_scores(text, chunk_type="prose"):
    """Score one chunk's semantic role from its local wording and structure.

    Args:
        text: Chunk text.
        chunk_type: ``"table"`` earns a structural property score, else ``"prose"``.

    Returns:
        Role name to normalized score.
    """
    raw = {role: cfg.ROLE_CHUNK_WEIGHTS[role] * len(pattern.findall(text or ""))
           for role, pattern in _CHUNK_PATTERNS.items()}
    if chunk_type == "table":
        raw["property"] += cfg.ROLE_TABLE_PROPERTY_BOOST
    return _normalize(raw)


def document_role_scores(texts):
    """Score a document's role from vocabulary density across all its text.

    Args:
        texts: Text of every chunk belonging to one document.

    Returns:
        Role name to normalized score.
    """
    joined = " ".join(texts)
    words = max(len(joined.split()), 1)
    raw = {role: cfg.ROLE_DOCUMENT_WEIGHTS[role] * len(pattern.findall(joined)) / words
           for role, pattern in _DOCUMENT_PATTERNS.items()}
    return _normalize(raw)
