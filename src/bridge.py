"""Build a small, inspectable alignment bridge between specification and RTL evidence.

The bridge links identities; it never decides whether RTL implements a requirement.  A link
exists only when the RTL states the spec name: the same flat name, or the name behind an
interface prefix the module's own ports share.
"""

from difflib import SequenceMatcher
import hashlib
from collections import Counter
import json
import math
from pathlib import Path
import re

import configure as cfg
from naming import identifier_tokens, normalize_identifier, normalize_module_name


SCHEMA_VERSION = "5.1"
STRONG = "STRONG"
MEDIUM = "MEDIUM"
UNRESOLVED = "UNRESOLVED"

_DIRECTION_TOKENS = frozenset({"i", "in", "input", "o", "out", "output"})
_TEXT_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_$]*")
_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "section", "signal", "signals", "table", "the", "this",
    "to", "with",
})
_MODULE_NAME_FILLERS = frozenset({"file", "state"})
# A bare suffix token can only name a port direction; the wider set also accepts declarations.
_PORT_DIRECTIONS = {"i": "input", "in": "input", "input": "input",
                    "o": "output", "out": "output", "output": "output"}
_DIRECTIONS = {**_PORT_DIRECTIONS, "io": "inout", "inout": "inout",
               "internal": "internal"}


def _unique(values):
    """Keep non-empty values once in encounter order.

    Args:
        values: Values to deduplicate.

    Returns:
        Unique non-empty values in first-seen order.
    """
    found = []
    for value in values:
        if value not in (None, "") and value not in found:
            found.append(value)
    return found


def _tokens(value, expand=True):
    """Return normalized content tokens for an identifier or short prose field.

    Args:
        value: Identifier or short prose to tokenize.
        expand: Expand configured abbreviations when true.

    Returns:
        Unique lowercase content tokens, stop words removed.
    """
    found = []
    for raw in _TEXT_TOKEN.findall(str(value or "")):
        parts = identifier_tokens(raw, expand_abbreviations=expand)
        found.extend(token for token in parts if token not in _STOP_WORDS)
    return _unique(found)


def _module_identity_tokens(values):
    """Normalize harmless morphology and container nouns for module-title matching.

    Args:
        values: Tokens taken from a section title or module name.

    Returns:
        Lowercase tokens, depluralized, with generic container nouns dropped.
    """
    normalized = []
    for value in values:
        token = str(value).lower()
        if token in _MODULE_NAME_FILLERS:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def _without_direction(tokens):
    """Remove one conventional input/output role token from an identifier tail.

    Args:
        tokens: Identifier tokens in source order.

    Returns:
        The tokens without a trailing direction token.
    """
    values = list(tokens)
    return values[:-1] if values and values[-1] in _DIRECTION_TOKENS else values


def _flat_name(name, expand):
    """Return one identifier flattened to its comparable spelling without a role suffix.

    Args:
        name: Source identifier spelling.
        expand: Expand configured abbreviations (``rm`` → ``rounding mode``) first.

    Returns:
        The identifier's lowercase tokens concatenated, direction suffix removed.
    """
    return "".join(_without_direction(identifier_tokens(name, expand_abbreviations=expand)))


def _direction(value):
    """Normalize common direction spellings, retaining internal when explicit.

    Args:
        value: Raw direction field.

    Returns:
        A canonical direction, or None when the value names none.
    """
    return _DIRECTIONS.get(str(value or "").strip().lower())


def _width(value, expression=None):
    """Parse a concrete bit width when one is directly available.

    Args:
        value: Raw width field, integer or text.
        expression: Optional range expression used as a fallback.

    Returns:
        A positive integer width, or None when none is stated outright.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    text = str(value or "").strip()
    if text.isdigit():
        parsed = int(text)
        return parsed if parsed > 0 else None
    ranged = re.fullmatch(r"\[?\s*(\d+)\s*:\s*(\d+)\s*\]?", text)
    if ranged:
        return abs(int(ranged.group(1)) - int(ranged.group(2))) + 1
    expr = str(expression or "")
    ranged = re.fullmatch(r"\[?\s*(\d+)\s*:\s*(\d+)\s*\]?", expr)
    return abs(int(ranged.group(1)) - int(ranged.group(2))) + 1 if ranged else None


def _jaccard(left, right):
    """Return set Jaccard similarity, with no evidence scoring zero.

    Args:
        left: First token collection.
        right: Second token collection.

    Returns:
        Similarity in ``[0, 1]``; zero when either side is empty.
    """
    a, b = set(left), set(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _edit_similarity(left, right):
    """Return deterministic character edit similarity in the closed unit interval.

    Args:
        left: First string.
        right: Second string.

    Returns:
        Similarity in ``[0, 1]``; zero when either side is empty.
    """
    return SequenceMatcher(None, str(left), str(right)).ratio() if left and right else 0.0


def _round(value):
    """Round scores consistently so persisted artifacts remain easy to diff.

    Args:
        value: Raw score.

    Returns:
        The score rounded to six decimal places.
    """
    return round(float(value), 6)


def _data_fingerprint(value):
    """Hash one decoded input deterministically for in-memory bridge builds.

    Args:
        value: JSON-compatible decoded artifact.

    Returns:
        A hex SHA-256 digest of its canonical serialization.
    """
    material = json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _path_fingerprint(path):
    """Hash an input artifact byte-for-byte for rebuild provenance.

    Args:
        path: Artifact file to hash.

    Returns:
        A hex SHA-256 digest of the file's bytes.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_ADDRESS = re.compile(r"(?:\d*'[hH]|0[xX])([0-9A-Fa-f]+)|([0-9A-Fa-f]+)[hH]|(\d+)")


def _address(cell):
    """Parse one register-address cell into an integer.

    Args:
        cell: Address as written: ``0x343``, ``343h``, ``12'h343``, or decimal ``835``.

    Returns:
        The address, or None for empty cells, ranges, and anything else.
    """
    match = _ADDRESS.fullmatch(str(cell or "").strip().replace("_", ""))
    if not match:
        return None
    digits = match.group(1) or match.group(2)
    return int(digits, 16) if digits else int(match.group(3))


def _canonical_spec_signal(raw):
    """Adapt one specification signal row into the bridge's internal shape.

    Args:
        raw: One ``spec_signals.json`` row.

    Returns:
        A canonical specification signal record; ``width`` is the cell parsed to bits
        when it states an integer or range, ``width_cell`` the cell as written.
    """
    name = raw["name"]
    return {
        "signal_id": raw["signal_id"],
        "original_name": name,
        "width": _width(raw.get("width")),
        "width_cell": raw.get("width"),
        "address": _address(raw.get("address")),
        "address_cell": raw.get("address"),
        "document_id": raw["document_id"],
        "hierarchy_id": raw["hierarchy_id"],
        "section_path": raw["section_path"],
        "table_id": raw.get("table_id"),
        "chunk_id": raw.get("chunk_id"),
        "page": raw.get("page"),
        "source_file": raw.get("source_file"),
    }

def spec_signals(data):
    """Return canonical specification signals sorted by stable identity.

    Args:
        data: Decoded ``spec_signals.json``.

    Returns:
        Canonical signals ordered by signal identifier.
    """
    return sorted((_canonical_spec_signal(raw) for raw in data["signals"]),
                  key=lambda row: row["signal_id"])

def _canonical_rtl_signal(raw, module):
    """Adapt one module-local RTL declaration into the bridge's internal shape.

    Args:
        raw: One ``rtl.json`` port or signal declaration.
        module: ``rtl.json`` module owning it.

    Returns:
        A canonical RTL signal record.
    """
    module_name = module["original_name"]
    original = raw["original_name"]
    canonical = {
        "signal_id": raw["signal_id"],
        "original_name": original,
        "normalized_name": raw.get("normalized_name") or normalize_identifier(original),
        "normalized_tokens": _unique(str(token).lower() for token in
                                     raw.get("normalized_tokens") or identifier_tokens(original)),
        "width": _width(raw.get("width"), raw.get("width_expression")),
        "width_expression": raw.get("width_expression"),
        "direction": _direction(raw.get("direction")) or "internal",
        "description": raw.get("description", ""),
        "module_id": module["module_id"],
        "module_name": module_name,
        "module_normalized_name": (module.get("normalized_name")
                                   or normalize_module_name(module_name)),
        "module_tokens": _unique(str(token).lower() for token in
                                 module.get("normalized_tokens") or identifier_tokens(module_name)),
        "file": raw["file_path"],
        "start_line": raw.get("start_line"),
        "end_line": raw.get("end_line", raw.get("start_line")),
        "reader_sites": raw.get("reader_sites", []),
        "writer_sites": raw.get("writer_sites", []),
    }
    canonical["similarity_tokens"] = _without_direction(identifier_tokens(original))
    return canonical

def rtl_modules(rtl):
    """Return canonical module records.

    Args:
        rtl: Decoded ``rtl.json``.

    Returns:
        Canonical module records ordered by module identifier.
    """
    modules = []
    for raw in rtl["modules"].values():
        name = raw["original_name"]
        modules.append({
            "module_id": raw["module_id"],
            "original_name": name,
            "normalized_name": raw.get("normalized_name") or normalize_module_name(name),
            "normalized_tokens": _unique(str(token).lower() for token in
                                         raw.get("normalized_tokens") or identifier_tokens(name)),
            "file": raw["file"],
            "start_line": raw.get("start_line"),
            "end_line": raw.get("end_line"),
        })
    return sorted(modules, key=lambda module: module["module_id"])

def rtl_signals(rtl):
    """Flatten module-local ports and signals into canonical scoped declarations.

    Args:
        rtl: Decoded ``rtl.json``.

    Returns:
        Canonical RTL signals ordered by signal identifier.
    """
    rows = [_canonical_rtl_signal(raw, module)
            for module in rtl["modules"].values()
            for raw in [*module.get("ports", []), *module.get("signals", [])]]
    return sorted(rows, key=lambda signal: signal["signal_id"])

def _rtl_provenance(rtl):
    """Return the source handles and bounded sites needed to inspect an RTL signal.

    Args:
        rtl: Canonical RTL signal.

    Returns:
        Provenance with the file extent, site counts, and bounded sites.
    """
    return {
        "file": rtl["file"], "start_line": rtl["start_line"], "end_line": rtl["end_line"],
        "reader_site_count": len(rtl["reader_sites"]),
        "writer_site_count": len(rtl["writer_sites"]),
        "reader_sites": rtl["reader_sites"][:cfg.BRIDGE_PROVENANCE_SITES],
        "writer_sites": rtl["writer_sites"][:cfg.BRIDGE_PROVENANCE_SITES],
    }


def _spec_provenance(spec):
    """Return the source handles required to inspect a specification signal.

    Args:
        spec: Canonical specification signal.

    Returns:
        The stated provenance fields, omitting those the signal leaves unset.
    """
    return {key: spec.get(key) for key in (
        "document_id", "hierarchy_id", "section_path", "table_id", "chunk_id", "page",
        "source_file") if spec.get(key) is not None}


def interface_prefixes(spec_flats, rtl_rows):
    """Learn each module's interface prefixes from its own ports.

    A prefix counts when at least ``cfg.BRIDGE_PREFIX_MIN_PORTS`` of a module's ports are
    that prefix followed by a specification name, as in ``m`` + ``awaddr`` = ``MAWAddr``.

    Args:
        spec_flats: Flat specification names.
        rtl_rows: Canonical RTL signals.

    Returns:
        ``(module_id, prefix)`` pairs.
    """
    families = {}
    for rtl in rtl_rows:
        if rtl["direction"] not in ("input", "output"):
            continue
        flat = _flat_name(rtl["original_name"], expand=False)
        for size in range(1, cfg.BRIDGE_PREFIX_MAX_CHARS + 1):
            rest = flat[size:]
            if len(rest) >= cfg.BRIDGE_PREFIX_MIN_NAME and rest in spec_flats:
                families.setdefault((rtl["module_id"], flat[:size]), set()).add(rest)
    return {key for key, names in families.items() if len(names) >= cfg.BRIDGE_PREFIX_MIN_PORTS}


def _match(rtl, spec_flats, prefixes):
    """Find the specification name one RTL signal states, and how.

    Args:
        rtl: Canonical RTL signal.
        spec_flats: Flat specification names.
        prefixes: Learned ``(module_id, prefix)`` pairs.

    Returns:
        ``(spec flat, method, prefix)``, or None when the signal states no spec name.
    """
    for expand in (False, True):
        flat = _flat_name(rtl["original_name"], expand=expand)
        if flat in spec_flats:
            return flat, "identity", None
    flat = _flat_name(rtl["original_name"], expand=False)
    # The longest name wins, so MARValid is ARVALID behind "m", not RVALID behind "ma".
    found = [(flat[len(prefix):], prefix) for module_id, prefix in prefixes
             if module_id == rtl["module_id"] and flat.startswith(prefix)
             and flat[len(prefix):] in spec_flats]
    if not found:
        return None
    rest, prefix = max(found, key=lambda item: (len(item[0]), item[1]))
    return rest, "interface_prefix", prefix


def _width_evidence(spec, rtl):
    """State how the specification width compares with the RTL width.

    Args:
        spec: Canonical specification signal.
        rtl: Canonical RTL signal.

    Returns:
        ``{spec, rtl, status}`` with status ``equal``, ``conflict``, ``symbolic`` (a
        non-integer spec cell), or ``unstated``.
    """
    cell, spec_width, rtl_width = spec["width_cell"], spec["width"], rtl["width"]
    if spec_width is not None and rtl_width is not None:
        status = "equal" if spec_width == rtl_width else "conflict"
    elif cell not in (None, ""):
        status = "symbolic"
    else:
        status = "unstated"
    return {"spec": cell, "rtl": rtl_width, "status": status}


def _link(spec, rtl, method, prefix):
    """Build one spec-to-RTL link row stating how it was found.

    Args:
        spec: Canonical specification signal.
        rtl: Canonical RTL signal.
        method: ``identity`` or ``interface_prefix``.
        prefix: Learned prefix for ``interface_prefix``, else None.

    Returns:
        One ``signal_alignments`` row.
    """
    return {
        "spec_signal_id": spec["signal_id"],
        "spec_original_name": spec["original_name"],
        "rtl_kind": "signal",
        "rtl_signal_id": rtl["signal_id"],
        "rtl_original_name": rtl["original_name"],
        "rtl_module_id": rtl["module_id"],
        "rtl_module_name": rtl["module_name"],
        "rtl_direction": rtl["direction"],
        "method": method,
        "prefix": prefix,
        "width": _width_evidence(spec, rtl),
        "confidence_level": STRONG if method == "identity" else MEDIUM,
        "provenance": {"spec": _spec_provenance(spec), "rtl": _rtl_provenance(rtl)},
    }


def _suggestions(spec, rtl_rows):
    """Rank the RTL signals whose names look like an unresolved spec name.

    Similarity is shown to a reader and never becomes a link.

    Args:
        spec: Canonical specification signal no tier linked.
        rtl_rows: Canonical RTL signals, each carrying its ``similarity_tokens``.

    Returns:
        Up to ``cfg.BRIDGE_QUERY_CANDIDATES`` ``SUGGESTED`` candidates, best first.
    """
    tokens = _without_direction(identifier_tokens(spec["original_name"]))
    flat = "".join(tokens)
    scored = []
    for rtl in rtl_rows:
        score = max(_jaccard(tokens, rtl["similarity_tokens"]),
                    _edit_similarity(flat, "".join(rtl["similarity_tokens"]))
                    * cfg.BRIDGE_EDIT_WEIGHT)
        if score > 0:
            scored.append((-score, rtl["signal_id"], rtl))
    return [{
        "status": "SUGGESTED",
        "rtl_signal_id": rtl["signal_id"],
        "rtl_original_name": rtl["original_name"],
        "rtl_module_name": rtl["module_name"],
        "score": _round(-negative),
        "width": _width_evidence(spec, rtl),
    } for negative, _, rtl in sorted(scored, key=lambda item: item[:2])[
        :cfg.BRIDGE_QUERY_CANDIDATES]]


def enum_members(rtl):
    """Flatten every declared enumeration member into a link target.

    Args:
        rtl: Decoded ``rtl.json``.

    Returns:
        Member rows ordered by enumeration identifier and member name.
    """
    return sorted(({"enum_id": enum["enum_id"], "enum_name": enum["original_name"],
                    "original_name": item["original_name"], "value": item.get("value"),
                    "value_source": item.get("value_source"),
                    "file": item.get("file"), "line": item.get("line")}
                   for enum in rtl.get("enums", []) for item in enum["items"]),
                  key=lambda row: (row["enum_id"], row["original_name"]))


def _address_evidence(spec, member):
    """State how a spec address cell compares with the member's declared value.

    Args:
        spec: Canonical specification signal.
        member: Row from ``enum_members``.

    Returns:
        ``{spec, rtl, status}`` with status ``equal``, ``conflict``, or ``symbolic``
        (cell not a single number), or None when the spec states no address.
    """
    if spec["address_cell"] is None:
        return None
    status = ("symbolic" if spec["address"] is None
              else "equal" if spec["address"] == member["value"] else "conflict")
    return {"spec": spec["address_cell"], "rtl": member["value_source"], "status": status}


def _member_link(spec, member, method="identity"):
    """Build one link row from a spec signal to the enum member that declares it.

    Args:
        spec: Canonical specification signal.
        member: Row from ``enum_members``.
        method: ``identity`` (same name) or ``address`` (same number in a confirmed map).

    Returns:
        One ``signal_alignments`` row with ``rtl_kind`` ``enum_member``.
    """
    return {
        "spec_signal_id": spec["signal_id"],
        "spec_original_name": spec["original_name"],
        "rtl_kind": "enum_member",
        "rtl_signal_id": f"{member['enum_id']}::{member['original_name']}",
        "rtl_original_name": member["original_name"],
        "rtl_enum_name": member["enum_name"],
        "rtl_member_name": member["original_name"],
        "rtl_member_value": member["value"],
        "method": method,
        "prefix": None,
        "address": _address_evidence(spec, member),
        "confidence_level": STRONG if method == "identity" else MEDIUM,
        "provenance": {"spec": _spec_provenance(spec),
                       "rtl": {"file": member["file"], "line": member["line"]}},
    }


def link_signals(specs, rtl_rows, members=()):
    """Link every RTL signal or enum member that states a specification name.

    Args:
        specs: Canonical specification signals.
        rtl_rows: Canonical RTL signals.
        members: Enumeration members from ``enum_members``; linked by identity only.

    Returns:
        ``(links, unresolved)``: link rows, and one row per spec signal with no link.
    """
    by_flat = {}
    for spec in specs:
        for expand in (False, True):
            rows = by_flat.setdefault(_flat_name(spec["original_name"], expand=expand), [])
            if spec not in rows:
                rows.append(spec)
    prefixes = interface_prefixes(set(by_flat), rtl_rows)
    links = []
    for rtl in rtl_rows:
        found = _match(rtl, by_flat, prefixes)
        if found:
            flat, method, prefix = found
            links.extend(_link(spec, rtl, method, prefix) for spec in by_flat[flat])
    # CSRs are declared as enumeration members (``csr_index_t.mstatus``), not as signals.
    named = []
    for member in members:
        for expand in (False, True):
            flat = _flat_name(member["original_name"], expand=expand)
            if flat in by_flat:
                named.extend((spec, member) for spec in by_flat[flat])
                break
    links.extend(_member_link(spec, member) for spec, member in named)
    # A table and an enum agreeing on name and number twice are one address map.
    agreements = Counter((spec["table_id"], member["enum_id"]) for spec, member in named
                         if spec["address"] is not None and spec["address"] == member["value"])
    maps = sorted(key for key, count in agreements.items()
                  if count >= cfg.BRIDGE_ADDRESS_MAP_MIN)
    numbered = {(member["enum_id"], member["value"]): member for member in members}
    named_ids = {spec["signal_id"] for spec, _ in named}
    for spec in specs:
        if spec["signal_id"] in named_ids or spec["address"] is None:
            continue
        member = next((numbered[(enum_id, spec["address"])] for table_id, enum_id in maps
                       if table_id == spec["table_id"]
                       and (enum_id, spec["address"]) in numbered), None)
        if member:
            links.append(_member_link(spec, member, "address"))
    linked = {link["spec_signal_id"] for link in links}
    unresolved = [{
        "spec_signal_id": spec["signal_id"],
        "spec_original_name": spec["original_name"],
        "confidence_level": UNRESOLVED,
        "reason": "no RTL signal states this name",
        "candidates": _suggestions(spec, rtl_rows),
        "provenance": {"spec": _spec_provenance(spec)},
    } for spec in specs if spec["signal_id"] not in linked]
    return (sorted(links, key=lambda row: (row["spec_signal_id"], row["rtl_signal_id"])),
            unresolved)

def _section_rows(hierarchy):
    """Return every section row of the hierarchy artifact.

    Args:
        hierarchy: Decoded ``spec_hierarchy.json``.

    Returns:
        Section rows in document order.
    """
    return [section for document in hierarchy["documents"]
            for section in document["sections"]]

def _canonical_section(raw):
    """Adapt one hierarchy section into the module-alignment surface.

    Args:
        raw: One ``spec_hierarchy.json`` section row.

    Returns:
        A canonical section record with its title, path, and provenance.
    """
    return {
        "section_id": raw["node_id"],
        "title": raw.get("subsection_title") or raw["section_name"],
        "path": list(raw["path"]),
        "document_id": raw.get("document_id"),
        "page": raw["start_page"],
        "source_file": raw["doc_name"],
    }

def _title_identity(section, module):
    """Tell whether a section title names a module, ignoring plurals and container nouns.

    Args:
        section: Canonical section record.
        module: Canonical RTL module record.

    Returns:
        True when the title and the module name are the same words.
    """
    title_tokens = _module_identity_tokens(_tokens(section["title"]))
    module_tokens = _module_identity_tokens(module["normalized_tokens"])
    return bool(title_tokens) and (
        normalize_module_name(section["title"]) == module["normalized_name"]
        or set(title_tokens) == set(module_tokens))


def derive_module_alignments(hierarchy, modules, alignments, specs):
    """Link sections to modules by title identity or by their own signals' links.

    Args:
        hierarchy: Decoded specification hierarchy artifact.
        modules: Canonical RTL module records.
        alignments: Signal link rows.
        specs: Canonical specification signals.

    Returns:
        One row per (section, module) with stated evidence, ordered by section then module;
        sections with none are not written.
    """
    rows = []
    for raw in _section_rows(hierarchy):
        section = _canonical_section(raw)
        scoped = {spec["signal_id"] for spec in specs
                  if spec["hierarchy_id"] == section["section_id"]}
        counts = {}
        for link in alignments:
            if link["spec_signal_id"] in scoped and link.get("rtl_module_id"):
                counts[link["rtl_module_id"]] = counts.get(link["rtl_module_id"], 0) + 1
        for module in modules:
            titled = _title_identity(section, module)
            if not titled and module["module_id"] not in counts:
                continue
            rows.append({
                "spec_section_id": section["section_id"],
                "spec_section_title": section["title"],
                "rtl_module_id": module["module_id"],
                "rtl_module_name": module["original_name"],
                "method": "title_identity" if titled else "link_support",
                "link_count": counts.get(module["module_id"], 0),
                "confidence_level": STRONG if titled else MEDIUM,
                "provenance": {"spec": {key: section[key] for key in (
                    "document_id", "page", "source_file") if section.get(key) is not None},
                               "rtl": {"file": module["file"],
                                       "start_line": module["start_line"],
                                       "end_line": module["end_line"]}},
            })
    return sorted(rows, key=lambda row: (row["spec_section_id"], row["rtl_module_id"]))

def build_bridge(spec_data, rtl_data, hierarchy_data, source_fingerprints=None):
    """Build the complete deterministic bridge artifact from decoded upstream artifacts.

    Args:
        spec_data: Decoded specification signal artifact.
        rtl_data: Decoded RTL artifact.
        hierarchy_data: Decoded specification hierarchy artifact.
        source_fingerprints: Optional precomputed input fingerprints.

    Returns:
        The complete ``bridge.json`` payload with its summary counts.
    """
    specs = spec_signals(spec_data)
    rtl_rows = rtl_signals(rtl_data)
    modules = rtl_modules(rtl_data)
    alignments, unresolved = link_signals(specs, rtl_rows, enum_members(rtl_data))
    module_alignments = derive_module_alignments(
        hierarchy_data, modules, alignments, specs)
    fingerprints = source_fingerprints or {
        "spec_signals": _data_fingerprint(spec_data),
        "rtl": _data_fingerprint(rtl_data),
        "spec_hierarchy": _data_fingerprint(hierarchy_data),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source_fingerprints": fingerprints,
        "signal_alignments": alignments,
        "unresolved_signals": unresolved,
        "module_alignments": module_alignments,
        "summary": {
            "spec_signals": len(specs), "rtl_signals": len(rtl_rows),
            "signal_alignments": len(alignments), "unresolved_signals": len(unresolved),
            "module_alignments": len(module_alignments),
        },
    }


def load_bridge(path=cfg.BRIDGE_PATH):
    """Load a bridge artifact, returning an empty compatible view when absent.

    Args:
        path: Bridge artifact path.

    Returns:
        The decoded artifact, or an empty artifact of the same shape.
    """
    if not Path(path).is_file():
        return {"schema_version": SCHEMA_VERSION, "source_fingerprints": {},
                "signal_alignments": [], "unresolved_signals": [],
                "module_alignments": [], "summary": {}}
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def _load_json(path):
    """Load a required JSON artifact with a concise missing-file failure.

    Args:
        path: Required artifact path.

    Returns:
        The decoded artifact.

    Raises:
        SystemExit: If the artifact does not exist.
    """
    if not Path(path).is_file():
        raise SystemExit(f"missing {path}; rebuild its upstream ingestion stage")
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def source_fingerprints():
    """Return current byte hashes for every upstream bridge input artifact.

    Returns:
        Artifact name to the hex digest of its current bytes.
    """
    return {
        "spec_signals": _path_fingerprint(cfg.SPEC_SIGNALS_PATH),
        "rtl": _path_fingerprint(cfg.RTL_PATH),
        "spec_hierarchy": _path_fingerprint(cfg.SPEC_HIERARCHY_PATH),
    }


def _write_bridge(artifact, path=cfg.BRIDGE_PATH):
    """Publish a bridge artifact atomically so readers never see partial JSON.

    Args:
        artifact: Complete bridge payload.
        path: Destination JSON path.

    Returns:
        None.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as output:
        json.dump(artifact, output, ensure_ascii=False, indent=1, sort_keys=True)
        output.write("\n")
    temporary.replace(destination)


def main():
    """Build every signal and module alignment into ``output/bridge.json``.

    Returns:
        None.
    """
    spec_data = _load_json(cfg.SPEC_SIGNALS_PATH)
    rtl_data = _load_json(cfg.RTL_PATH)
    hierarchy_data = _load_json(cfg.SPEC_HIERARCHY_PATH)
    artifact = build_bridge(spec_data, rtl_data, hierarchy_data,
                            source_fingerprints=source_fingerprints())
    _write_bridge(artifact)
    summary = artifact["summary"]
    print(f"====  wrote {cfg.BRIDGE_PATH.relative_to(cfg.ROOT)}: "
          f"{summary['signal_alignments']} signal alignments, "
          f"{summary['unresolved_signals']} unresolved, "
          f"{summary['module_alignments']} section/module alignments")


if __name__ == "__main__":
    main()
