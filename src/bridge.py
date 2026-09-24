"""Build a small, inspectable alignment bridge between specification and RTL evidence.

The bridge links identities; it never decides whether RTL implements a requirement.  A link
exists only when the RTL states the spec name: the same flat name, or the name behind an
interface prefix the module's own ports share.
"""

from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re

import configure as cfg
from ingest import content_hash
from naming import identifier_tokens, normalize_identifier, normalize_module_name


SCHEMA_VERSION = "4.0"
STRONG = "STRONG"
MEDIUM = "MEDIUM"
WEAK = "WEAK"
UNRESOLVED = "UNRESOLVED"
PORT_ROW = "port"
REGISTER_FIELD_ROW = "register_field"

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


def _row_kind(value):
    """Normalize the declaration class a specification row describes.

    Args:
        value: Raw row-kind field.

    Returns:
        ``register_field`` when stated, otherwise ``port``.
    """
    folded = str(value or "").strip().lower()
    return (folded if folded in (PORT_ROW, REGISTER_FIELD_ROW)
            else PORT_ROW)


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


def _canonical_spec_signal(raw):
    """Adapt one specification signal row into the bridge's internal shape.

    Args:
        raw: One ``spec_signals.json`` row.

    Returns:
        A canonical specification signal record.
    """
    original = raw["original_name"]
    return {
        "signal_id": raw["signal_id"],
        "original_name": original,
        "normalized_name": raw.get("normalized_name") or normalize_identifier(original),
        "normalized_tokens": _unique(str(token).lower() for token in
                                     raw.get("normalized_tokens") or identifier_tokens(original)),
        "width": _width(raw.get("width")),
        "width_cell": raw.get("width"),
        "row_kind": _row_kind(raw.get("row_kind")),
        "description": raw.get("description") or "",
        "document_id": raw.get("document_id"),
        "section_id": raw.get("section_id"),
        "section_title": raw.get("section_title"),
        "subsection_id": raw.get("subsection_id"),
        "subsection_title": raw.get("subsection_title"),
        "table_id": raw.get("table_id"),
        "table_title": raw.get("table_caption"),
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

def _module_blocks(module):
    """Collect a module's structural blocks.

    Args:
        module: ``rtl.json`` module record.

    Returns:
        Its always, function/task, assignment-group, and other blocks.
    """
    return [block for key in ("always_blocks", "functions_tasks", "assignment_groups",
                              "other_blocks") for block in module.get(key, [])]

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
            "description": "",
            "file": raw["file"],
            "start_line": raw.get("start_line"),
            "end_line": raw.get("end_line"),
            "raw": raw,
        })
    return sorted(modules, key=lambda module: module["module_id"])

def port_nets(rtl):
    """Group ports that the elaborated netlist shows are one signal threaded through modules.

    A port bound to the same parent-scope net as another module's port is not a competing
    alignment target: both name one wire. Bindings are transitive, so a net seen at two
    hierarchy levels merges into one group.

    Args:
        rtl: Decoded RTL artifact.

    Returns:
        ``(module_name, port_name)`` to a stable net identifier, for threaded ports only.
    """
    owned = {module["original_name"]: {port["original_name"] for port in module.get("ports", [])}
             for module in rtl["modules"].values()}
    groups = []
    for module in rtl["modules"].values():
        scoped = {}
        for instance in module.get("instances", []):
            target = instance["target_module"]
            for binding in instance["bindings"]:
                formal = binding["formal_name"]
                parent = binding["parent_module"]
                for actual in binding["actual_signals"]:
                    members = scoped.setdefault((parent, str(actual)), set())
                    members.add((str(target), str(formal)))
                    # A parent port used as the actual continues the same net one level up.
                    if str(actual) in owned.get(parent, ()):
                        members.add((parent, str(actual)))
        groups.extend(scoped.values())
    merged = {}
    for group in groups:
        found = [existing for existing in merged.values() if existing & group]
        union = set(group).union(*found)
        for member in union:
            merged[member] = union
    nets = {}
    for member, union in merged.items():
        if len(union) > 1:
            nets[member] = "bridge::net::" + content_hash(
                *sorted(f"{module}.{port}" for module, port in union))
    return nets


def rtl_signals(rtl):
    """Flatten module-local ports and signals into canonical scoped declarations.

    Args:
        rtl: Decoded ``rtl.json``.

    Returns:
        Canonical RTL signals ordered by signal identifier.
    """
    nets = port_nets(rtl)
    rows = []
    for module in rtl["modules"].values():
        for raw in [*module.get("ports", []), *module.get("signals", [])]:
            signal = _canonical_rtl_signal(raw, module)
            signal["net_id"] = nets.get((signal["module_name"], signal["original_name"]))
            rows.append(signal)
    return sorted(rows, key=lambda signal: signal["signal_id"])

def _component(score, weight, available, detail):
    """Describe one understandable structural score component.

    Args:
        score: Component score in ``[0, 1]``.
        weight: Configured component weight.
        available: Whether both sides stated enough to score this component.
        detail: Human-readable explanation of what was compared.

    Returns:
        One component-evidence mapping.
    """
    return {"score": _round(score), "weight": _round(weight),
            "available": bool(available), "detail": detail}


def _weighted_components(components):
    """Average available score components by configured weight.

    Args:
        components: Component name to component evidence.

    Returns:
        The weighted mean of available components, or zero when none are.
    """
    active = [component for component in components.values() if component["available"]]
    weight = sum(component["weight"] for component in active)
    return (sum(component["score"] * component["weight"] for component in active) / weight
            if weight else 0.0)


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


def confidence_level(score):
    """Map a numeric accepted alignment score to the configured trust band.

    Args:
        score: Accepted alignment score.

    Returns:
        ``STRONG``, ``MEDIUM``, ``WEAK``, or ``UNRESOLVED``.
    """
    if score >= cfg.BRIDGE_STRONG_MIN:
        return STRONG
    if score >= cfg.BRIDGE_MEDIUM_MIN:
        return MEDIUM
    if score >= cfg.BRIDGE_ACCEPT_MIN:
        return WEAK
    return UNRESOLVED


def _spec_provenance(spec):
    """Return the source handles required to inspect a specification signal.

    Args:
        spec: Canonical specification signal.

    Returns:
        The stated provenance fields, omitting those the signal leaves unset.
    """
    return {key: spec.get(key) for key in (
        "document_id", "section_id", "section_title", "subsection_id", "subsection_title",
        "table_id", "chunk_id", "page", "source_file") if spec.get(key) is not None}


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


def link_signals(specs, rtl_rows):
    """Link every RTL signal that states a specification name to those spec rows.

    Args:
        specs: Canonical specification signals.
        rtl_rows: Canonical RTL signals.

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
    linked = {link["spec_signal_id"] for link in links}
    unresolved = [{
        "spec_signal_id": spec["signal_id"],
        "spec_original_name": spec["original_name"],
        "confidence_level": UNRESOLVED,
        "reason": "no RTL signal states this name",
        "candidates": [],
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
        "description": "",
        "document_id": raw.get("document_id"),
        "page": raw["start_page"],
        "source_file": raw["doc_name"],
    }

def _module_candidate(section, module, scoped_alignments, scoped_specs):
    """Score one section-to-module candidate using names, context, and child mappings.

    Args:
        section: Canonical section record.
        module: Canonical RTL module record.
        scoped_alignments: Signal alignments belonging to this section.
        scoped_specs: Specification signals belonging to this section.

    Returns:
        A scored candidate record carrying its eligibility flag.
    """
    title_tokens = _module_identity_tokens(_tokens(section["title"]))
    module_tokens = _module_identity_tokens(module["normalized_tokens"])
    token_score = _jaccard(title_tokens, module_tokens)
    edit = _edit_similarity("".join(title_tokens), "".join(module_tokens))
    name_score = max(token_score, edit * cfg.BRIDGE_EDIT_WEIGHT)
    if (normalize_module_name(section["title"]) == module["normalized_name"]
            or set(title_tokens) == set(module_tokens)):
        name_score = 1.0
    spec_context = _tokens(" ".join([*section["path"], section["description"],
                                      *(spec.get("description", "") for spec in scoped_specs)]))
    module_signal_text = " ".join(
        signal["original_name"] for key in ("ports", "signals")
        for signal in module["raw"].get(key, []))
    module_context = _tokens(" ".join((module["normalized_name"], module["description"],
                                       module_signal_text)))
    context_available = bool(spec_context and module_context)
    context_score = _jaccard(spec_context, module_context)
    child = [alignment for alignment in scoped_alignments
             if alignment["rtl_module_id"] == module["module_id"]]
    child_score = len(child) / len(scoped_alignments) if scoped_alignments else 0.0
    components = {
        "normalized_title": _component(name_score, cfg.BRIDGE_MODULE_WEIGHTS["name"], True,
                                       "section title vs normalized module name"),
        "context": _component(context_score, cfg.BRIDGE_MODULE_WEIGHTS["context"],
                              context_available,
                              "section path/descriptions vs module/signals"),
        "child_signals": _component(child_score,
                                    cfg.BRIDGE_MODULE_WEIGHTS["child_signals"],
                                    bool(scoped_alignments),
                                    f"{len(child)} of {len(scoped_alignments)} aligned signals"),
    }
    return {
        "rtl_module_id": module["module_id"],
        "rtl_module_name": module["original_name"],
        "score": _round(_weighted_components(components)),
        "methods": _unique((["normalized_title"] if name_score > 0 else []) +
                           (["context_overlap"] if context_score > 0 else []) +
                           (["child_signal_support"] if child else [])),
        "component_evidence": components,
        "child_spec_signal_ids": [alignment["spec_signal_id"] for alignment in child],
        "provenance": {"file": module["file"], "start_line": module["start_line"],
                       "end_line": module["end_line"]},
        "eligible": (name_score >= cfg.BRIDGE_MODULE_NAME_MIN or bool(child) or
                     context_score >= cfg.BRIDGE_MODULE_CONTEXT_MIN),
    }


def derive_module_alignments(hierarchy, modules, alignments, specs):
    """Derive inspectable section-to-module alignments from names, context, and child signals.

    Args:
        hierarchy: Decoded specification hierarchy artifact.
        modules: Canonical RTL module records.
        alignments: Accepted signal alignments.
        specs: Canonical specification signals.

    Returns:
        Section alignment records ordered by section identifier.
    """
    by_spec_id = {spec["signal_id"]: spec for spec in specs}
    results = []
    for raw in _section_rows(hierarchy):
        section = _canonical_section(raw)
        if not section["section_id"] or not section["title"]:
            continue
        scoped_specs = [spec for spec in specs if section["section_id"] in
                        {spec.get("section_id"), spec.get("subsection_id")}]
        scoped_ids = {spec["signal_id"] for spec in scoped_specs}
        scoped_alignments = [alignment for alignment in alignments
                             if alignment["spec_signal_id"] in scoped_ids]
        candidates = [_module_candidate(section, module, scoped_alignments, scoped_specs)
                      for module in modules]
        candidates = [candidate for candidate in candidates if candidate["eligible"]]
        candidates.sort(key=lambda candidate: (-candidate["score"],
                                               candidate["rtl_module_id"]))
        candidates = candidates[:cfg.BRIDGE_MODULE_CANDIDATES]
        for rank, candidate in enumerate(candidates, 1):
            candidate["candidate_rank"] = rank
            candidate.pop("eligible", None)
        if not candidates:
            continue
        top_name_score = candidates[0]["component_evidence"]["normalized_title"]["score"]
        if (not scoped_specs and not scoped_alignments
                and top_name_score < cfg.BRIDGE_MODULE_PERSIST_NAME_MIN):
            continue
        top = candidates[0]
        ambiguous = (len(candidates) > 1 and
                     top["score"] - candidates[1]["score"] <= cfg.BRIDGE_AMBIGUITY_MARGIN)
        accepted = top["score"] >= cfg.BRIDGE_MODULE_ACCEPT_MIN and not ambiguous
        entry = {
            "alignment_id": ("bridge::module::" + content_hash(
                section["section_id"], top["rtl_module_id"]) if accepted else None),
            "spec_section_id": section["section_id"],
            "spec_section_title": section["title"],
            "rtl_module_id": top["rtl_module_id"] if accepted else None,
            "rtl_module_name": top["rtl_module_name"] if accepted else None,
            "confidence": top["score"] if accepted else 0.0,
            "confidence_level": confidence_level(top["score"]) if accepted else UNRESOLVED,
            "methods": top["methods"] if accepted else [],
            "candidate_rank": top["candidate_rank"] if accepted else None,
            "component_evidence": top["component_evidence"] if accepted else {},
            "ambiguous": ambiguous,
            "candidates": candidates,
            "provenance": {"spec": {key: section[key] for key in (
                "document_id", "page", "source_file") if section.get(key) is not None},
                           "child_spec_signal_ids": sorted(by_spec_id[signal_id]["signal_id"]
                                                           for signal_id in scoped_ids)},
        }
        if not accepted:
            entry["reason"] = ("top module candidates are too close to choose safely" if ambiguous
                               else "best module candidate is below the acceptance threshold")
        results.append(entry)
    return sorted(results, key=lambda entry: entry["spec_section_id"])


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
    alignments, unresolved = link_signals(specs, rtl_rows)
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
            "module_alignments": sum(item["rtl_module_id"] is not None
                                     for item in module_alignments),
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
