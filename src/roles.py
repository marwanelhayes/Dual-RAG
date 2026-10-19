"""Score the normative role of whole documents by wording alone.

Flow: Count document-level role vocabulary -> apply configured weights -> normalize authority scores.
"""

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
