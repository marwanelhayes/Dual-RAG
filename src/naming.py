"""Deterministically normalize hardware identifiers without losing source spelling."""

import re


# Vendor-neutral only; domain vocabulary arrives from config via ``set_abbreviation_aliases``.
ABBREVIATION_ALIASES = {
    "addr": ("address",),
    "cfg": ("configuration",),
    "clk": ("clock",),
    "ctrl": ("control",),
    "dst": ("destination",),
    "exc": ("exception",),
    "req": ("request",),
    "resp": ("response",),
    "rst": ("reset",),
    "src": ("source",),
}

# Role letters mean a direction only as a suffix; ``ICacheHit`` splits an ``I`` that does not.
SUFFIX_ALIASES = {
    "i": ("input",),
    "o": ("output",),
}

_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_LETTER_TO_DIGIT = re.compile(r"([A-Za-z])([0-9])")
_DIGIT_TO_LETTER = re.compile(r"([0-9])([A-Za-z])")
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")


def _raw_tokens(name):
    """Split an identifier at case, digit, and punctuation boundaries.

    Args:
        name: Source identifier or descriptive name.

    Returns:
        Lowercase identifier tokens in source order.
    """
    text = str(name or "").strip()
    text = _ACRONYM_BOUNDARY.sub(r"\1 \2", text)
    text = _CAMEL_BOUNDARY.sub(r"\1 \2", text)
    text = _LETTER_TO_DIGIT.sub(r"\1 \2", text)
    text = _DIGIT_TO_LETTER.sub(r"\1 \2", text)
    text = _SEPARATORS.sub(" ", text)
    return [token.lower() for token in text.split() if token]


def set_abbreviation_aliases(aliases, replace=False):
    """Configure deterministic abbreviation expansion.

    Args:
        aliases: Mapping of abbreviation to a string or sequence of expansion tokens.
        replace: Clear the built-in mapping before installing the supplied aliases.

    Returns:
        A copy of the active normalized alias mapping.
    """
    if replace:
        ABBREVIATION_ALIASES.clear()
    for abbreviation, expansion in aliases.items():
        key_tokens = _raw_tokens(abbreviation)
        if len(key_tokens) != 1:
            raise ValueError(f"abbreviation must normalize to one token: {abbreviation!r}")
        values = _raw_tokens(expansion if isinstance(expansion, str)
                             else " ".join(str(value) for value in expansion))
        if not values:
            raise ValueError(f"abbreviation expansion cannot be empty: {abbreviation!r}")
        ABBREVIATION_ALIASES[key_tokens[0]] = tuple(values)
    return dict(ABBREVIATION_ALIASES)


def normalize_identifier(name: object) -> str:
    """Return a stable snake-case retrieval form of a source identifier.

    Args:
        name: Original identifier spelling.

    Returns:
        Lowercase tokens joined by underscores, without abbreviation expansion.
    """
    return "_".join(_raw_tokens(name))


def identifier_tokens(name: object, expand_abbreviations: bool = True) -> list[str]:
    """Return deterministic retrieval tokens for a hardware identifier.

    Args:
        name: Original identifier spelling.
        expand_abbreviations: Expand configured whole-token aliases when true.

    Returns:
        Lowercase tokens, with configured abbreviations expanded in place.
    """
    tokens = _raw_tokens(name)
    if not expand_abbreviations:
        return tokens
    expanded = []
    for position, token in enumerate(tokens):
        suffix = SUFFIX_ALIASES if position and position == len(tokens) - 1 else {}
        expanded.extend(suffix.get(token) or ABBREVIATION_ALIASES.get(token, (token,)))
    return expanded


def normalize_module_name(name: object) -> str:
    """Return an expanded, space-joined module name for retrieval and alignment.

    Args:
        name: Original RTL module identifier.

    Returns:
        Expanded normalized module-name tokens joined by spaces.
    """
    return " ".join(identifier_tokens(name, expand_abbreviations=True))
