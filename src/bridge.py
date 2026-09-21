"""Build a small, inspectable alignment bridge between specification and RTL evidence.

The bridge aligns identities; it never decides whether RTL implements a requirement.  Cheap
name probes generate several candidates, structured metadata narrows them, an optional pair
scorer reranks only the survivors, and an optional tiny-model callback breaks close ties.
"""

import argparse
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re

import configure as cfg
from embed import content_hash
from naming import identifier_tokens, normalize_identifier, normalize_module_name


SCHEMA_VERSION = "4.0"
STRONG = "STRONG"
MEDIUM = "MEDIUM"
WEAK = "WEAK"
UNRESOLVED = "UNRESOLVED"
PORT_ROW = "port"
REGISTER_FIELD_ROW = "register_field"
INSTRUCTION_ROW = "instruction"
_OPPOSITE_ROLE = {"manager": "subordinate", "subordinate": "manager"}
_ROLE_VOTE_MIN = 2

_DIRECTION_TOKENS = frozenset({"i", "in", "input", "o", "out", "output"})
_NAMESPACE_PREFIXES = frozenset({
    "m", "dm", "im", "s", "cpu", "csr", "dst", "fp", "fpu", "src",
})
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


def _as_list(value):
    """Return a schema field as a list without splitting scalar strings.

    Args:
        value: Field value of any shape.

    Returns:
        The value as a list; a scalar becomes a one-element list.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _first(record, *names, default=None):
    """Return the first present, non-null field from a mapping.

    Args:
        record: Mapping to read.
        *names: Field names tried in preference order.
        default: Value returned when no name is present and non-null.

    Returns:
        The first non-null field value, or ``default``.
    """
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return default


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


def _flat_name(name):
    """Return one identifier flattened to its comparable spelling without a role suffix.

    Args:
        name: Source identifier spelling.

    Returns:
        The identifier's tokens concatenated, direction suffix removed.
    """
    return "".join(_without_direction(identifier_tokens(name, expand_abbreviations=False)))


def _namespace_prefixed(spec_flat, rtl_flat):
    """Report an RTL name that is a specification name behind one known namespace prefix.

    Args:
        spec_flat: Flattened specification identifier.
        rtl_flat: Flattened RTL identifier.

    Returns:
        True when the RTL name is the spec name behind a known prefix.
    """
    return (len(spec_flat) >= 4 and rtl_flat.endswith(spec_flat)
            and rtl_flat[:-len(spec_flat)] in _NAMESPACE_PREFIXES)


def _suffix_direction(name):
    """Infer input/output from a conventional final identifier token when present.

    Args:
        name: Source identifier spelling.

    Returns:
        ``input``, ``output``, or None when the tail names no direction.
    """
    tokens = identifier_tokens(name, expand_abbreviations=False)
    return _PORT_DIRECTIONS.get(tokens[-1]) if tokens else None


def _direction(value):
    """Normalize common direction spellings, retaining internal when explicit.

    Args:
        value: Raw direction field.

    Returns:
        A canonical direction, or None when the value names none.
    """
    return _DIRECTIONS.get(str(value or "").strip().lower())


def _source_role(value):
    """Normalize the interface role a specification names as one signal's driver.

    Args:
        value: Raw source or driver field.

    Returns:
        ``manager``, ``subordinate``, or None when no role is named.
    """
    folded = str(value or "").strip().lower()
    return {"manager": "manager", "master": "manager", "initiator": "manager",
            "requester": "manager", "transmitter": "manager", "tx": "manager",
            "subordinate": "subordinate", "slave": "subordinate", "target": "subordinate",
            "completer": "subordinate", "receiver": "subordinate",
            "rx": "subordinate"}.get(folded)


def _row_kind(value):
    """Normalize the declaration class a specification row describes.

    Args:
        value: Raw row-kind field.

    Returns:
        One of the three known row kinds, defaulting to ``port``.
    """
    folded = str(value or "").strip().lower()
    return (folded if folded in (PORT_ROW, REGISTER_FIELD_ROW, INSTRUCTION_ROW)
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


def _spec_signal_rows(data):
    """Return signal rows from the canonical object or reasonable migration aliases.

    Args:
        data: Decoded specification signal artifact.

    Returns:
        The raw signal rows, or an empty list when none are present.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("signals", "spec_signals", "entities", "records"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def _canonical_spec_signal(raw, ordinal):
    """Adapt one specification signal into the bridge's stable internal shape.

    Args:
        raw: One raw specification signal row.
        ordinal: Row position, used only for the fallback identifier.

    Returns:
        A canonical specification signal record.
    """
    original = str(_first(raw, "original_name", "name", "signal_name", "display", default=""))
    normalized = str(_first(raw, "normalized_name", default="") or
                     normalize_identifier(original))
    raw_tokens = _first(raw, "normalized_tokens", "tokens")
    tokens = _as_list(raw_tokens) if raw_tokens is not None else identifier_tokens(original)
    section_id = _first(raw, "section_id", "section")
    subsection_id = _first(raw, "subsection_id")
    table_id = _first(raw, "table_id", "table", "table_label")
    fallback = content_hash(str(_first(raw, "document_id", "doc_id", "source_file", default="")),
                            str(section_id or subsection_id or table_id or ""), original,
                            str(ordinal))
    signal_id = str(_first(raw, "signal_id", "entity_id", "id",
                           default=f"spec::signal::{fallback}"))
    return {
        "signal_id": signal_id,
        "original_name": original,
        "normalized_name": normalized,
        "normalized_tokens": _unique(str(token).lower() for token in tokens),
        "width": _width(_first(raw, "width", "bit_width"),
                        _first(raw, "width_expression", "range", "bit_range")),
        "width_expression": _first(raw, "width_expression", "range", "bit_range"),
        "direction": _direction(_first(raw, "direction", "dir")),
        "source_role": _source_role(_first(raw, "source_role", "source", "driven_by")),
        "row_kind": _row_kind(_first(raw, "row_kind", "kind")),
        "description": str(_first(raw, "description", "details", "text", default="")),
        "document_id": _first(raw, "document_id", "doc_id"),
        "section_id": section_id,
        "section_title": _first(raw, "section_title", "section_name"),
        "subsection_id": subsection_id,
        "subsection_title": _first(raw, "subsection_title", "subsection_name"),
        "table_id": table_id,
        "table_title": _first(raw, "table_title", "table_caption", "caption"),
        "chunk_id": _first(raw, "chunk_id", "source_chunk_id"),
        "page": _first(raw, "page", "start_page"),
        "source_file": _first(raw, "source_file", "file", "doc_name"),
    }


def spec_signals(data):
    """Return canonical specification signals sorted by stable identity.

    Args:
        data: Decoded specification signal artifact.

    Returns:
        Named canonical signals ordered by signal identifier.
    """
    rows = [_canonical_spec_signal(raw, ordinal)
            for ordinal, raw in enumerate(_spec_signal_rows(data)) if isinstance(raw, dict)]
    return sorted((row for row in rows if row["original_name"]),
                  key=lambda row: row["signal_id"])


def _module_rows(rtl):
    """Return ``(fallback_name, module)`` pairs from dict- or list-shaped RTL artifacts.

    Args:
        rtl: Decoded RTL artifact.

    Returns:
        ``(fallback name, raw module)`` pairs, empty when the shape is unknown.
    """
    raw = rtl.get("modules", {}) if isinstance(rtl, dict) else {}
    if isinstance(raw, dict):
        return [(str(name), module) for name, module in raw.items() if isinstance(module, dict)]
    if isinstance(raw, list):
        return [(str(_first(module, "original_name", "name", "module_name", default="")),
                 module) for module in raw if isinstance(module, dict)]
    return []


def _site_rows(signal, *names):
    """Collect unique read/write site mappings from compatible field aliases.

    Args:
        signal: Raw RTL declaration.
        *names: Field names holding site lists.

    Returns:
        Unique site mappings in encounter order.
    """
    rows = []
    for name in names:
        for site in _as_list(signal.get(name)):
            if isinstance(site, dict) and site not in rows:
                rows.append(site)
    return rows


def _module_blocks(module):
    """Collect structural blocks without depending on one upstream grouping spelling.

    Args:
        module: Raw RTL module record.

    Returns:
        Unique structural block mappings in encounter order.
    """
    blocks = []
    for key in ("always_blocks", "functions_tasks", "assignment_groups", "other_blocks",
                "blocks", "structural_blocks"):
        for block in _as_list(module.get(key)):
            if isinstance(block, dict) and block not in blocks:
                blocks.append(block)
    return blocks


def _signal_usage(signal, module):
    """Render bounded deterministic usage context for one RTL signal.

    Args:
        signal: Partially built canonical RTL signal.
        module: Raw RTL module owning that signal.

    Returns:
        Bounded usage text describing how the signal is read and written.
    """
    name = signal["original_name"]
    parts = [signal.get("description", "")]
    if signal["reader_sites"]:
        parts.append("read by RTL logic")
    if signal["writer_sites"]:
        parts.append("written or driven by RTL logic")
    for block in _module_blocks(module):
        reads = _as_list(_first(block, "signals_read", "reads", "inputs", default=[]))
        writes = _as_list(_first(block, "signals_written", "writes", "outputs", default=[]))
        if name not in reads and name not in writes:
            continue
        parts.extend(str(value) for value in (
            _first(block, "construct_type", "kind"),
            _first(block, "original_name", "name"),
        ) if value)
        if len(parts) >= 8:
            break
    return " ".join(_unique(part for part in parts if part))[:cfg.BRIDGE_CONTEXT_CHARS]


def _canonical_rtl_signal(raw, module, fallback_name, ordinal):
    """Adapt one module-local RTL declaration into the bridge's stable shape.

    Args:
        raw: One raw RTL declaration.
        module: Raw RTL module owning it.
        fallback_name: Module name used when the record states none.
        ordinal: Declaration position, used only for the fallback identifier.

    Returns:
        A canonical RTL signal record including its usage context.
    """
    module_name = str(_first(module, "original_name", "name", "module_name",
                             default=fallback_name))
    original = str(_first(raw, "original_name", "name", "signal_name", "symbol_name",
                          default=""))
    normalized = str(_first(raw, "normalized_name", default="") or
                     normalize_identifier(original))
    raw_tokens = _first(raw, "normalized_tokens", "tokens")
    tokens = _as_list(raw_tokens) if raw_tokens is not None else identifier_tokens(original)
    signal_id = str(_first(raw, "signal_id", "symbol_id", "record_id", "id",
                           default=f"rtl::signal::{module_name}::{original}::{ordinal}"))
    readers = _site_rows(raw, "reader_sites", "read_sites", "readers", "reads")
    writers = _site_rows(raw, "writer_sites", "write_sites", "drivers", "writes")
    canonical = {
        "signal_id": signal_id,
        "original_name": original,
        "normalized_name": normalized,
        "normalized_tokens": _unique(str(token).lower() for token in tokens),
        "width": _width(_first(raw, "width", "bit_width"),
                        _first(raw, "width_expression", "range", "bit_range")),
        "width_expression": _first(raw, "width_expression", "range", "bit_range"),
        "direction": _direction(_first(raw, "direction", "dir")) or "internal",
        "description": str(_first(raw, "description", "comment", "summary", default="")),
        "module_id": str(_first(module, "module_id", "id",
                                default=f"rtl::mod::{module_name}")),
        "module_name": module_name,
        "module_normalized_name": str(_first(module, "normalized_name", default="") or
                                      normalize_module_name(module_name)),
        "module_tokens": _unique(str(token).lower() for token in
                                 (_as_list(_first(module, "normalized_tokens", "tokens")) or
                                  identifier_tokens(module_name))),
        "file": _first(raw, "file", "file_path", default=_first(module, "file", "file_path")),
        "start_line": _first(raw, "start_line", "line"),
        "end_line": _first(raw, "end_line", default=_first(raw, "start_line", "line")),
        "reader_sites": readers,
        "writer_sites": writers,
    }
    canonical["usage"] = _signal_usage(canonical, module)
    return canonical


def rtl_modules(rtl):
    """Return canonical module records with compatible structural field aliases.

    Args:
        rtl: Decoded RTL artifact.

    Returns:
        Canonical module records ordered by module identifier.
    """
    modules = []
    for fallback_name, raw in _module_rows(rtl):
        name = str(_first(raw, "original_name", "name", "module_name", default=fallback_name))
        raw_tokens = _first(raw, "normalized_tokens", "tokens")
        tokens = _as_list(raw_tokens) if raw_tokens is not None else identifier_tokens(name)
        modules.append({
            "module_id": str(_first(raw, "module_id", "id", default=f"rtl::mod::{name}")),
            "original_name": name,
            "normalized_name": str(_first(raw, "normalized_name", default="") or
                                   normalize_module_name(name)),
            "normalized_tokens": _unique(str(token).lower() for token in tokens),
            "description": str(_first(raw, "description", "summary", "comments", default="")),
            "file": _first(raw, "file", "file_path"),
            "start_line": _first(raw, "start_line", "line"),
            "end_line": _first(raw, "end_line"),
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
    owned = {}
    for fallback, module in _module_rows(rtl):
        name = str(_first(module, "original_name", "name", "module_name", default=fallback))
        owned[name] = {str(_first(port, "original_name", "name", "symbol_name", default=""))
                       for port in _as_list(module.get("ports"))}
    groups = []
    for _, module in _module_rows(rtl):
        scoped = {}
        for instance in _as_list(module.get("instances")):
            target = instance.get("target_module")
            for binding in _as_list(instance.get("bindings")):
                formal = binding.get("formal_name")
                if not target or not formal:
                    continue
                parent = str(binding.get("parent_module") or "")
                for actual in _as_list(binding.get("actual_signals")):
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
        rtl: Decoded RTL artifact.

    Returns:
        Canonical RTL signals, deduplicated and ordered by signal identifier.
    """
    rows = []
    seen = set()
    nets = port_nets(rtl)
    for fallback_name, module in _module_rows(rtl):
        candidates = []
        for key in ("ports", "signals", "declarations", "symbol_records"):
            candidates.extend(item for item in _as_list(module.get(key)) if isinstance(item, dict))
        for ordinal, raw in enumerate(candidates):
            signal = _canonical_rtl_signal(raw, module, fallback_name, ordinal)
            if not signal["original_name"] or signal["signal_id"] in seen:
                continue
            signal["net_id"] = nets.get(
                (signal["module_name"], signal["original_name"]))
            seen.add(signal["signal_id"])
            rows.append(signal)
    return sorted(rows, key=lambda signal: signal["signal_id"])


def enum_members(rtl):
    """Flatten every declared enumeration member into a comparable row.

    Args:
        rtl: Decoded RTL artifact.

    Returns:
        Member rows ordered by enumeration identifier and member name.
    """
    rows = []
    for enum in _as_list(rtl.get("enums")) if isinstance(rtl, dict) else []:
        if not isinstance(enum, dict):
            continue
        for item in _as_list(enum.get("items")):
            if not isinstance(item, dict) or not item.get("original_name"):
                continue
            original = str(item["original_name"])
            rows.append({
                "enum_id": enum.get("enum_id"),
                "enum_name": enum.get("original_name"),
                "original_name": original,
                "normalized_name": str(item.get("normalized_name") or "")
                                   or normalize_identifier(original),
                "normalized_tokens": identifier_tokens(original),
                "value": item.get("value"),
                "file": item.get("file"),
                "line": item.get("line"),
                "usage_sites": _as_list(item.get("usage_sites")),
            })
    return sorted(rows, key=lambda row: (str(row["enum_id"]), row["original_name"]))


def align_enum(spec, members):
    """Align one specification instruction row to an identically named member.

    Specification mnemonics and RTL enumeration members normalize onto the same
    identifier, so identity decides the mapping and no rival candidate is ranked.

    Args:
        spec: Canonical specification instruction row.
        members: Enumeration member rows from ``enum_members``.

    Returns:
        An enumeration alignment record, or None when no member matches.
    """
    match = next((member for member in members
                  if member["normalized_name"] == spec["normalized_name"]), None)
    if match is None:
        return None
    implemented = 1.0 if match["usage_sites"] else 0.0
    components = {
        "name": _component(1.0, 0.7, True, "normalized instruction identity"),
        "implementation": _component(
            implemented, 0.3, True, f"{len(match['usage_sites'])} usage site(s)"),
    }
    score = _round(_weighted_components(components))
    return {
        "alignment_id": "bridge::enum::" + content_hash(
            spec["signal_id"], str(match["enum_id"]), match["original_name"]),
        "spec_signal_id": spec["signal_id"],
        "spec_original_name": spec["original_name"],
        "spec_normalized_name": spec["normalized_name"],
        "rtl_enum_id": match["enum_id"],
        "rtl_enum_name": match["enum_name"],
        "rtl_member_name": match["original_name"],
        "rtl_member_value": match["value"],
        "confidence": score,
        "confidence_level": confidence_level(score),
        "methods": _unique(["normalized_name"] +
                           (["implementing_site"] if match["usage_sites"] else [])),
        "component_evidence": components,
        "provenance": {"spec": _spec_provenance(spec), "rtl": {
            "file": match["file"], "line": match["line"],
            "usage_site_count": len(match["usage_sites"]),
            "usage_sites": match["usage_sites"][:cfg.BRIDGE_PROVENANCE_SITES],
        }},
    }


def module_roles(specs, rtl_rows):
    """Infer each module's interface role from spec-stated signal sources.

    A module drives the signals its own role sources, so an identically named port
    votes for the spec role when it is an output and for the opposite role when it
    is an input.  Only deterministic name identity casts a vote, and a module keeps
    a role only when several ports agree, so one generic name cannot decide it.

    Args:
        specs: Canonical specification signals.
        rtl_rows: Canonical RTL signals to vote with.

    Returns:
        Module identifier to its inferred role, only where the vote is decisive.
    """
    stated = {}
    for spec in specs:
        if spec["source_role"]:
            stated.setdefault(_flat_name(spec["original_name"]), spec["source_role"])
    votes = {}
    for rtl in rtl_rows:
        if rtl["direction"] not in ("input", "output"):
            continue
        flat = _flat_name(rtl["original_name"])
        role = next((value for name, value in stated.items()
                     if name == flat or _namespace_prefixed(name, flat)), None)
        if not role:
            continue
        inferred = role if rtl["direction"] == "output" else _OPPOSITE_ROLE[role]
        tally = votes.setdefault(rtl["module_id"], {})
        tally[inferred] = tally.get(inferred, 0) + 1
    return {module_id: max(sorted(tally), key=tally.get)
            for module_id, tally in votes.items()
            if sum(tally.values()) >= _ROLE_VOTE_MIN
            and max(tally.values()) * 2 > sum(tally.values())}


def with_module_roles(rtl_rows, specs):
    """Return RTL rows annotated with the interface role inferred for their module.

    Args:
        rtl_rows: Canonical RTL signals.
        specs: Canonical specification signals.

    Returns:
        Copies of the RTL rows carrying a ``module_role`` field.
    """
    roles = module_roles(specs, rtl_rows)
    return [{**rtl, "module_role": roles.get(rtl["module_id"])} for rtl in rtl_rows]


def expected_direction(spec, rtl):
    """Return the port direction a specification implies for one candidate module.

    Args:
        spec: Canonical specification signal.
        rtl: Candidate canonical RTL signal.

    Returns:
        The implied direction, or None when neither side states enough.
    """
    if spec["direction"]:
        return spec["direction"]
    role, module_role = spec["source_role"], rtl.get("module_role")
    if not role or not module_role:
        return None
    return "output" if role == module_role else "input"


def cheap_name_evidence(spec, rtl):
    """Measure every deterministic layer-one name probe for one signal pair.

    Args:
        spec: Canonical specification signal.
        rtl: Candidate canonical RTL signal.

    Returns:
        Per-probe evidence with the best ``score`` and the ``methods`` that fired.
    """
    spec_original, rtl_original = spec["original_name"], rtl["original_name"]
    spec_normal, rtl_normal = spec["normalized_name"], rtl["normalized_name"]
    spec_raw = _without_direction(identifier_tokens(spec_original, expand_abbreviations=False))
    rtl_raw = _without_direction(identifier_tokens(rtl_original, expand_abbreviations=False))
    spec_expanded = _without_direction(spec["normalized_tokens"] or identifier_tokens(spec_original))
    rtl_expanded = _without_direction(rtl["normalized_tokens"] or identifier_tokens(rtl_original))
    namespace_prefix = _namespace_prefixed("".join(spec_raw), "".join(rtl_raw))
    token_overlap = _jaccard(spec_expanded, rtl_expanded)
    edit = _edit_similarity("".join(spec_expanded), "".join(rtl_expanded))
    evidence = {
        "exact_name": spec_original == rtl_original,
        "normalized_name": bool(spec_normal and spec_normal == rtl_normal),
        "direction_suffix": bool(spec_raw and rtl_raw and spec_raw == rtl_raw and
                                 spec_normal != rtl_normal),
        "abbreviation_expansion": bool(spec_expanded and spec_expanded == rtl_expanded and
                                       spec_raw != rtl_raw),
        "namespace_prefix": namespace_prefix,
        "token_overlap": _round(token_overlap),
        "edit_similarity": _round(edit),
    }
    scored = [
        1.0 if evidence["exact_name"] else 0.0,
        0.97 if evidence["normalized_name"] else 0.0,
        0.92 if evidence["direction_suffix"] else 0.0,
        0.9 if evidence["abbreviation_expansion"] else 0.0,
        0.94 if evidence["namespace_prefix"] else 0.0,
        token_overlap,
        edit * cfg.BRIDGE_EDIT_WEIGHT,
    ]
    evidence["score"] = _round(max(scored))
    evidence["methods"] = [name for name in (
        "exact_name", "normalized_name", "direction_suffix", "abbreviation_expansion",
        "namespace_prefix")
        if evidence[name]]
    if token_overlap > 0:
        evidence["methods"].append("token_overlap")
    if edit >= cfg.BRIDGE_EDIT_MIN:
        evidence["methods"].append("edit_similarity")
    return evidence


def generate_signal_candidates(spec, rtl_rows, limit=None):
    """Generate several cheap candidates without selecting a mapping.

    Args:
        spec: Canonical specification signal.
        rtl_rows: Canonical RTL signals to search.
        limit: Optional candidate cap; defaults to the configured maximum.

    Returns:
        Scored candidate wrappers, best cheap score first.
    """
    ranked = []
    for rtl in rtl_rows:
        evidence = cheap_name_evidence(spec, rtl)
        comparable = (spec["row_kind"] != REGISTER_FIELD_ROW) == (rtl["direction"] != "internal")
        eligible = (evidence["exact_name"] or evidence["normalized_name"] or
                    (comparable and
                     (evidence["direction_suffix"] or evidence["abbreviation_expansion"] or
                      evidence["namespace_prefix"] or
                      evidence["token_overlap"] >= cfg.BRIDGE_TOKEN_MIN or
                      evidence["edit_similarity"] >= cfg.BRIDGE_EDIT_MIN)))
        ranked.append({"rtl": rtl, "cheap": evidence, "eligible": eligible})
    ranked.sort(key=lambda item: (-item["cheap"]["score"], item["rtl"]["signal_id"]))
    selected = [item for item in ranked if item["eligible"]]
    minimum = min(cfg.BRIDGE_MIN_CANDIDATES, len(ranked))
    if len(selected) < minimum:
        fallback = [item for item in ranked if item not in selected]
        selected.extend(fallback[:minimum - len(selected)])
    cap = limit or cfg.BRIDGE_MAX_CANDIDATES
    return selected[:cap]


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


def structural_signal_score(spec, candidate):
    """Score one layer-one candidate using source and compiler metadata.

    Args:
        spec: Canonical specification signal.
        candidate: Candidate wrapper carrying its RTL row and cheap evidence.

    Returns:
        ``(structural score, component evidence)``.
    """
    rtl, cheap = candidate["rtl"], candidate["cheap"]
    weights = cfg.BRIDGE_SIGNAL_WEIGHTS
    spec_width, rtl_width = spec["width"], rtl["width"]
    width_available = spec_width is not None and rtl_width is not None
    width_score = 1.0 if width_available and spec_width == rtl_width else 0.0
    spec_direction, rtl_direction = expected_direction(spec, rtl), rtl["direction"]
    inferred = _suffix_direction(rtl["original_name"])
    comparable_rtl_direction = rtl_direction if rtl_direction != "internal" else inferred
    direction_available = spec_direction is not None and comparable_rtl_direction is not None
    direction_score = 1.0 if direction_available and spec_direction == comparable_rtl_direction else 0.0
    role_detail = (f" via role {spec['source_role']}/{rtl.get('module_role')}"
                   if not spec["direction"] and spec_direction else "")
    spec_section = " ".join(str(value) for value in (
        spec.get("section_title"), spec.get("subsection_title")) if value)
    section_tokens = _tokens(spec_section)
    module_tokens = rtl["module_tokens"]
    section_available = bool(section_tokens and module_tokens)
    section_score = _jaccard(section_tokens, module_tokens)
    description_tokens = _tokens(spec.get("description"))
    usage_tokens = _tokens(" ".join((rtl.get("description", ""), rtl.get("usage", ""))))
    usage_available = bool(description_tokens and usage_tokens)
    usage_score = _jaccard(description_tokens, usage_tokens)
    table_text = spec.get("table_title")
    table_tokens = _tokens(table_text)
    table_target = _tokens(" ".join((rtl["module_normalized_name"], rtl.get("usage", ""))))
    table_available = bool(table_tokens and table_target)
    table_score = _jaccard(table_tokens, table_target)
    components = {
        "name": _component(cheap["score"], weights["name"], True,
                           ", ".join(cheap["methods"]) or "weak nearest-name fallback"),
        "width": _component(width_score, weights["width"], width_available,
                            f"spec={spec_width!r}, rtl={rtl_width!r}"),
        "direction": _component(direction_score, weights["direction"], direction_available,
                                f"spec={spec_direction!r}{role_detail}, "
                                f"rtl={comparable_rtl_direction!r}"),
        "section_module": _component(section_score, weights["section_module"],
                                     section_available,
                                     f"section={spec_section!r}, module={rtl['original_name']!r}"),
        "description_usage": _component(usage_score, weights["description_usage"],
                                        usage_available,
                                        "spec description vs compiler-derived RTL usage"),
        "table_context": _component(table_score, weights["table_context"], table_available,
                                    "table caption vs RTL module/usage context"),
    }
    return _round(_weighted_components(components)), components


def _spec_pair_text(spec):
    """Render compact contextual specification evidence for semantic scoring.

    Args:
        spec: Canonical specification signal.

    Returns:
        Bounded text describing the specification side of the pair.
    """
    return "\n".join(str(part) for part in (
        f"Signal: {spec['original_name']}",
        f"Width: {spec['width']}" if spec["width"] is not None else "",
        f"Direction: {spec['direction']}" if spec["direction"] else "",
        f"Section: {spec.get('section_title') or ''} {spec.get('subsection_title') or ''}".strip(),
        f"Table: {spec.get('table_id') or ''} {spec.get('table_title') or ''}".strip(),
        f"Description: {spec.get('description') or ''}",
    ) if part and not str(part).endswith(": "))[:cfg.BRIDGE_CONTEXT_CHARS]


def _rtl_pair_text(rtl):
    """Render compact contextual RTL evidence for semantic scoring.

    Args:
        rtl: Canonical RTL signal.

    Returns:
        Bounded text describing the RTL side of the pair.
    """
    return "\n".join(str(part) for part in (
        f"Signal: {rtl['original_name']}",
        f"Width: {rtl['width']}" if rtl["width"] is not None else "",
        f"Direction: {rtl['direction']}",
        f"Module: {rtl['module_name']}",
        f"Used in: {rtl.get('usage') or ''}",
    ) if part and not str(part).endswith(": "))[:cfg.BRIDGE_CONTEXT_CHARS]


def _score_adapter(scorer, pairs):
    """Invoke a callable or ``score_pairs`` adapter and validate result cardinality.

    Args:
        scorer: Pair scorer, adapter object, or None.
        pairs: Text pairs to score.

    Returns:
        One float per pair, or an empty list when no scorer is configured.

    Raises:
        ValueError: If the scorer returns a different number of scores than pairs.
    """
    if scorer is None:
        return []
    raw = scorer.score_pairs(pairs) if hasattr(scorer, "score_pairs") else scorer(pairs)
    scores = list(raw)
    if len(scores) != len(pairs):
        raise ValueError("bridge scorer returned a different number of scores than pairs")
    return [float(score) for score in scores]


def _semantic_unit(value):
    """Keep probability-like scores and squash arbitrary logits deterministically.

    Args:
        value: Raw scorer output.

    Returns:
        A score in ``[0, 1]``.
    """
    if 0.0 <= value <= 1.0:
        return value
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))


def _width_conflict(spec, candidate):
    """Report a stated width that contradicts the candidate beyond doubt.

    Names that are identical outright are trusted over the widths, so a genuine
    width disagreement stays visible as evidence instead of discarding the pair.

    Args:
        spec: Canonical specification signal.
        candidate: Candidate wrapper to test.

    Returns:
        True only when both widths are stated integers that disagree.
    """
    cheap, rtl = candidate["cheap"], candidate["rtl"]
    if cheap["exact_name"] or cheap["normalized_name"]:
        return False
    return (isinstance(spec["width"], int) and isinstance(rtl["width"], int)
            and spec["width"] != rtl["width"])


def rerank_signal_candidates(spec, candidates, scorer=None):
    """Apply layers two and three, scoring only structural survivors semantically.

    Args:
        spec: Canonical specification signal.
        candidates: Candidate wrappers from ``generate_signal_candidates``.
        scorer: Optional semantic pair scorer.

    Returns:
        The same candidates, scored, ranked, and numbered in place.
    """
    for candidate in candidates:
        score, components = structural_signal_score(spec, candidate)
        candidate["structural_score"] = score
        candidate["component_evidence"] = components
        candidate["rejected_by"] = "width" if _width_conflict(spec, candidate) else None
        candidate["survived_structural"] = (score >= cfg.BRIDGE_STRUCTURAL_MIN
                                            and candidate["rejected_by"] is None)
        candidate["semantic_score"] = None
        candidate["final_score"] = score
    survivors = [candidate for candidate in candidates if candidate["survived_structural"]]
    if scorer is not None and survivors:
        pairs = [(_spec_pair_text(spec), _rtl_pair_text(candidate["rtl"]))
                 for candidate in survivors]
        for candidate, raw_score in zip(survivors, _score_adapter(scorer, pairs)):
            semantic = _semantic_unit(raw_score)
            candidate["semantic_score"] = _round(semantic)
            candidate["final_score"] = _round(
                (1.0 - cfg.BRIDGE_SEMANTIC_WEIGHT) * candidate["structural_score"]
                + cfg.BRIDGE_SEMANTIC_WEIGHT * semantic)
            candidate["cheap"]["methods"].append("semantic_rerank")
    candidates.sort(key=lambda item: (-item["final_score"], item["rtl"]["signal_id"]))
    for rank, candidate in enumerate(candidates, 1):
        candidate["candidate_rank"] = rank
    return candidates


def _public_candidate(candidate):
    """Project an internal scored candidate into inspectable persisted evidence.

    Args:
        candidate: Internal scored candidate wrapper.

    Returns:
        A JSON-compatible candidate record with its scores and provenance.
    """
    rtl = candidate["rtl"]
    return {
        "candidate_rank": candidate["candidate_rank"],
        "rtl_signal_id": rtl["signal_id"],
        "rtl_original_name": rtl["original_name"],
        "rtl_normalized_name": rtl["normalized_name"],
        "rtl_module_id": rtl["module_id"],
        "rtl_module_name": rtl["module_name"],
        "width": rtl["width"],
        "direction": rtl["direction"],
        "methods": _unique(candidate["cheap"]["methods"] + ["structural_score"]),
        "layer1_score": candidate["cheap"]["score"],
        "structural_score": candidate["structural_score"],
        "semantic_score": candidate["semantic_score"],
        "rejected_by": candidate["rejected_by"],
        "score": _round(candidate["final_score"]),
        "survived_structural": candidate["survived_structural"],
        "cheap_evidence": {key: value for key, value in candidate["cheap"].items()
                           if key != "methods"},
        "component_evidence": candidate["component_evidence"],
        "provenance": _rtl_provenance(rtl),
    }


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


def ambiguity_evidence(spec, candidates, max_chars=None):
    """Render one strictly bounded temporary evidence file for an ambiguous signal.

    Args:
        spec: Canonical specification signal.
        candidates: Close candidates offered to the resolver.
        max_chars: Optional cap, never exceeding the configured maximum.

    Returns:
        Bounded plain-text evidence describing the signal and its candidates.
    """
    limit = min(max_chars or cfg.BRIDGE_LLM_MAX_CHARS, cfg.BRIDGE_LLM_MAX_CHARS)
    lines = ["SPEC SIGNAL", "", f"id: {spec['signal_id']}",
             f"name: {spec['original_name']}", f"width: {spec['width']}",
             f"direction: {spec['direction']}",
             f"section: {spec.get('section_title') or ''} / {spec.get('subsection_title') or ''}",
             f"description: {spec.get('description') or ''}", "", "RTL CANDIDATES"]
    for candidate in candidates[:cfg.BRIDGE_LLM_MAX_CANDIDATES]:
        rtl = candidate["rtl"]
        lines.extend(["", f"{candidate['candidate_rank']}.", f"id: {rtl['signal_id']}",
                      f"name: {rtl['original_name']}", f"width: {rtl['width']}",
                      f"direction: {rtl['direction']}", f"module: {rtl['module_name']}",
                      f"used in: {rtl.get('usage') or ''}",
                      f"deterministic score: {candidate['final_score']}"])
    lines.extend(["", "Choose only a candidate representing the same architectural concept.",
                  "Return candidate_id, confidence, reason; return candidate_id null if unsure."])
    return "\n".join(lines)[:limit]


def _resolver_result(resolver, evidence, candidates):
    """Validate a tiny-model resolver response against the offered candidate IDs.

    Args:
        resolver: Callable taking evidence text and returning a decision.
        evidence: Bounded evidence text passed to the resolver.
        candidates: Candidates the resolver was allowed to choose between.

    Returns:
        ``{candidate_id, confidence, reason}``, or None when the reply is unusable.
    """
    raw = resolver(evidence)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    selected = _first(raw, "candidate_id", "rtl_signal_id", "candidate")
    if isinstance(selected, int):
        indexed = {candidate["candidate_rank"]: candidate["rtl"]["signal_id"]
                   for candidate in candidates}
        selected = indexed.get(selected)
    offered = {candidate["rtl"]["signal_id"] for candidate in candidates}
    if selected not in offered:
        return None
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"candidate_id": selected, "confidence": _round(confidence),
            "reason": str(raw.get("reason", ""))[:cfg.BRIDGE_LLM_REASON_CHARS]}


def _implementation_sites(candidate):
    """Count compiler-observed sites that read or write one candidate signal.

    Args:
        candidate: Internal scored candidate carrying its RTL record.

    Returns:
        Combined reader and writer site count.
    """
    rtl = candidate["rtl"]
    return len(rtl["reader_sites"]) + len(rtl["writer_sites"])


def _collapse_threaded_ports(candidates):
    """Reduce ports carrying one elaborated net to the single site that drives it.

    Args:
        candidates: Close-scoring internal candidates.

    Returns:
        One representative per net when every candidate shares it, else the input.
    """
    nets = {candidate["rtl"].get("net_id") for candidate in candidates}
    if len(candidates) < 2 or len(nets) != 1 or None in nets:
        return candidates
    return [max(candidates, key=lambda candidate: (
        len(candidate["rtl"]["writer_sites"]), _implementation_sites(candidate),
        -candidate["candidate_rank"]))]


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


def align_signal(spec, rtl_rows, scorer=None, llm_resolver=None):
    """Run all enabled layers and return either an alignment or unresolved record.

    Args:
        spec: Canonical specification signal.
        rtl_rows: Canonical RTL signals to align against.
        scorer: Optional semantic pair scorer.
        llm_resolver: Optional tiny-model tie-breaker.

    Returns:
        ``("aligned", record)`` or ``("unresolved", record)``.
    """
    candidates = rerank_signal_candidates(
        spec, generate_signal_candidates(spec, rtl_rows), scorer=scorer)
    survivors = [candidate for candidate in candidates if candidate["survived_structural"]]
    public = [_public_candidate(candidate) for candidate in candidates]
    base = {
        "spec_signal_id": spec["signal_id"],
        "spec_original_name": spec["original_name"],
        "spec_normalized_name": spec["normalized_name"],
        "provenance": {"spec": _spec_provenance(spec)},
        "candidates": public,
    }
    def unresolved(reason, ambiguous, **extra):
        """Return the shared unresolved outcome carrying this rejection's reason.

        Args:
            reason: Why no candidate was accepted.
            ambiguous: Whether the top candidates were too close to choose between.
            **extra: Additional diagnostic fields for this rejection.

        Returns:
            ``("unresolved", record)``.
        """
        return "unresolved", {**base, "confidence": 0.0, "confidence_level": UNRESOLVED,
                              "ambiguous": ambiguous, "reason": reason, **extra}

    if not survivors:
        contradicted = candidates and all(item["rejected_by"] for item in candidates)
        return unresolved("every candidate contradicts the specified width" if contradicted
                          else "no candidate survived structural scoring", False)
    top = survivors[0]
    close = [candidate for candidate in survivors
             if top["final_score"] - candidate["final_score"] <= cfg.BRIDGE_AMBIGUITY_MARGIN]
    # Ports threaded through the hierarchy are one wire, so they never compete with each other.
    close = _collapse_threaded_ports(close)
    top = close[0] if len(close) == 1 else top
    # Observed usage separates a driving module from identically named pass-through ports.
    most_used = [candidate for candidate in close
                 if _implementation_sites(candidate)
                 == max(_implementation_sites(item) for item in close)]
    if len(most_used) == 1:
        top = most_used[0]
        close = most_used
    ambiguous = len(close) > 1
    resolution = None
    selected = top
    if ambiguous and llm_resolver is not None:
        evidence = ambiguity_evidence(spec, close)
        resolution = _resolver_result(llm_resolver, evidence, close)
        if resolution:
            selected = next(candidate for candidate in close
                            if candidate["rtl"]["signal_id"] == resolution["candidate_id"])
    score = selected["final_score"]
    if score < cfg.BRIDGE_ACCEPT_MIN:
        return unresolved("best candidate is below the acceptance threshold", ambiguous,
                          best_candidate_score=_round(score))
    if ambiguous and resolution is None:
        return unresolved("top candidates are too close to choose safely", True,
                          best_candidate_score=_round(score))
    if resolution is not None:
        score = min(score, resolution["confidence"])
        if score < cfg.BRIDGE_ACCEPT_MIN:
            return unresolved("tiny-model resolution confidence is too low", True,
                              resolution=resolution,
                              best_candidate_score=_round(selected["final_score"]))
    rtl = selected["rtl"]
    alignment_id = "bridge::signal::" + content_hash(spec["signal_id"], rtl["signal_id"])
    methods = _unique(selected["cheap"]["methods"] + ["structural_score"] +
                      (["tiny_llm_resolution"] if resolution else []))
    return "aligned", {
        **base,
        "alignment_id": alignment_id,
        "rtl_signal_id": rtl["signal_id"],
        "rtl_original_name": rtl["original_name"],
        "rtl_normalized_name": rtl["normalized_name"],
        "rtl_module_id": rtl["module_id"],
        "rtl_module_name": rtl["module_name"],
        "confidence": _round(score),
        "confidence_level": confidence_level(score),
        "methods": methods,
        "candidate_rank": selected["candidate_rank"],
        "component_evidence": selected["component_evidence"],
        "ambiguity": {"was_ambiguous": ambiguous,
                      "margin": cfg.BRIDGE_AMBIGUITY_MARGIN,
                      "resolver_used": resolution is not None,
                      "resolution": resolution},
        "provenance": {"spec": _spec_provenance(spec), "rtl": _rtl_provenance(rtl)},
    }


def _section_rows(hierarchy):
    """Return section rows from canonical hierarchy or legacy node aliases.

    Args:
        hierarchy: Decoded specification hierarchy artifact.

    Returns:
        Raw section rows, deduplicated by their stated identity.
    """
    if isinstance(hierarchy, list):
        return hierarchy
    if not isinstance(hierarchy, dict):
        return []
    rows = [section for section in _as_list(hierarchy.get("sections"))
            if isinstance(section, dict)]
    for document in _as_list(hierarchy.get("documents")):
        if not isinstance(document, dict):
            continue
        for section in _as_list(document.get("sections")):
            if not isinstance(section, dict):
                continue
            merged = dict(section)
            for key in ("document_id", "source_file", "doc_name"):
                if document.get(key) is not None:
                    merged.setdefault(key, document[key])
            rows.append(merged)
    rows.extend(node for node in _as_list(hierarchy.get("nodes"))
                if isinstance(node, dict) and
                str(node.get("type", "section")).lower() == "section")
    unique = {}
    for ordinal, row in enumerate(rows):
        identity = str(_first(row, "node_id", "id", "section_id", default=ordinal))
        unique.setdefault(identity, row)
    return list(unique.values())


def _canonical_section(raw):
    """Adapt one hierarchy section into the module-alignment surface.

    Args:
        raw: One raw hierarchy section row.

    Returns:
        A canonical section record with its title, path, and provenance.
    """
    section_id = str(_first(raw, "node_id", "id", "section_id", default=""))
    title = str(_first(raw, "subsection_title", "section_name", "section_title", "title",
                       "raw_title", "name",
                       default=""))
    path = _first(raw, "section_path", "path", "breadcrumb", default=[])
    return {
        "section_id": section_id,
        "title": title,
        "path": [str(value) for value in _as_list(path)],
        "description": str(_first(raw, "description", "summary", "text", default="")),
        "document_id": _first(raw, "document_id", "doc_id"),
        "page": _first(raw, "page", "start_page"),
        "source_file": _first(raw, "source_file", "file", "doc_name"),
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
        str(_first(signal, "original_name", "name", "signal_name", default=""))
        for key in ("ports", "signals") for signal in _as_list(module["raw"].get(key))
        if isinstance(signal, dict))
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
        "child_signal_alignment_ids": [alignment["alignment_id"] for alignment in child],
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
        if not isinstance(raw, dict):
            continue
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


def build_bridge(spec_data, rtl_data, hierarchy_data, scorer=None, llm_resolver=None,
                 source_fingerprints=None):
    """Build the complete deterministic bridge artifact from decoded upstream artifacts.

    Args:
        spec_data: Decoded specification signal artifact.
        rtl_data: Decoded RTL artifact.
        hierarchy_data: Decoded specification hierarchy artifact.
        scorer: Optional semantic pair scorer.
        llm_resolver: Optional tiny-model tie-breaker.
        source_fingerprints: Optional precomputed input fingerprints.

    Returns:
        The complete ``bridge.json`` payload with its summary counts.
    """
    every_spec = spec_signals(spec_data)
    instructions = [row for row in every_spec if row["row_kind"] == INSTRUCTION_ROW]
    specs = [row for row in every_spec if row["row_kind"] != INSTRUCTION_ROW]
    members = enum_members(rtl_data)
    enum_alignments = [record for record in
                       (align_enum(spec, members) for spec in instructions)
                       if record is not None]
    rtl_rows = with_module_roles(rtl_signals(rtl_data), specs)
    modules = rtl_modules(rtl_data)
    resolver_calls = 0

    def bounded_resolver(evidence):
        """Permit at most one configured tiny-model ambiguity decision per build.

        Args:
            evidence: Bounded evidence text for one ambiguous signal.

        Returns:
            The resolver's reply, or None once the per-build budget is spent.
        """
        nonlocal resolver_calls
        if llm_resolver is None or resolver_calls >= cfg.BRIDGE_LLM_MAX_CALLS:
            return None
        resolver_calls += 1
        return llm_resolver(evidence)

    alignments, unresolved = [], []
    for spec in specs:
        outcome, record = align_signal(spec, rtl_rows, scorer=scorer,
                                       llm_resolver=(bounded_resolver
                                                     if llm_resolver is not None else None))
        (alignments if outcome == "aligned" else unresolved).append(record)
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
        "signal_alignments": sorted(alignments, key=lambda item: item["spec_signal_id"]),
        "unresolved_signals": sorted(unresolved, key=lambda item: item["spec_signal_id"]),
        "enum_alignments": sorted(enum_alignments, key=lambda item: item["spec_signal_id"]),
        "module_alignments": module_alignments,
        "summary": {
            "spec_signals": len(specs), "rtl_signals": len(rtl_rows),
            "spec_instructions": len(instructions), "enum_members": len(members),
            "enum_alignments": len(enum_alignments),
            "unaligned_instructions": len(instructions) - len(enum_alignments),
            "signal_alignments": len(alignments), "unresolved_signals": len(unresolved),
            "module_alignments": sum(item["rtl_module_id"] is not None
                                     for item in module_alignments),
            "llm_fallback_calls": resolver_calls,
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


def lookup_signal(bridge, name_or_id, include_unresolved=False):
    """Return bridge rows matching a spec or RTL signal ID/name.

    Args:
        bridge: Decoded bridge artifact.
        name_or_id: Specification or RTL signal identifier or name.
        include_unresolved: Also search unresolved signals when true.

    Returns:
        Matching alignment rows, empty when nothing matches.
    """
    normalized = normalize_identifier(name_or_id)
    rows = list(bridge.get("signal_alignments", []))
    if include_unresolved:
        rows.extend(bridge.get("unresolved_signals", []))
    return [row for row in rows if name_or_id in {
        row.get("spec_signal_id"), row.get("rtl_signal_id")
    } or normalized in {
        normalize_identifier(row.get("spec_original_name", "")),
        normalize_identifier(row.get("rtl_original_name", "")),
    }]


def lookup_module(bridge, name_or_id, include_unresolved=False):
    """Return section-to-module rows matching either side of the bridge.

    Args:
        bridge: Decoded bridge artifact.
        name_or_id: Section or module identifier or name.
        include_unresolved: Also return rows with no accepted module when true.

    Returns:
        Matching module alignment rows, empty when nothing matches.
    """
    normalized = normalize_module_name(name_or_id)
    return [row for row in bridge.get("module_alignments", [])
            if (include_unresolved or row.get("rtl_module_id") is not None) and (
                name_or_id in {row.get("spec_section_id"), row.get("rtl_module_id")} or
                normalized in {
                    normalize_module_name(row.get("spec_section_title", "")),
                    normalize_module_name(row.get("rtl_module_name", "")),
                })]


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


def ollama_resolver(evidence):
    """Ask the configured tiny local model to resolve one bounded ambiguous shortlist.

    Args:
        evidence: Bounded evidence text for one ambiguous signal.

    Returns:
        The model's raw reply text, left unparsed so a truncated reply stays recoverable.
    """
    import httpx

    schema = {"type": "object", "properties": {
        "candidate_id": {"type": ["string", "null"]},
        "confidence": {"type": "number"}, "reason": {"type": "string"}},
        "required": ["candidate_id", "confidence", "reason"]}
    body = {"model": cfg.LOCAL_LLM_MODEL, "stream": False, "think": False,
            "format": schema,
            "messages": [{"role": "system", "content": (
                "Align identities only. Choose a candidate only when the evidence says it is "
                "the same architectural concept; otherwise return null. Output JSON only.")},
                         {"role": "user", "content": evidence[:cfg.BRIDGE_LLM_MAX_CHARS]}],
            "options": {"temperature": 0, "num_ctx": cfg.AGENT_NUM_CTX,
                        "num_predict": cfg.BRIDGE_LLM_MAX_TOKENS}}
    host = str(cfg.LOCAL_LLM_HOST).rstrip("/")
    response = httpx.post(f"{host}/api/chat", json=body, timeout=cfg.AGENT_TIMEOUT_S)
    response.raise_for_status()
    # Hand back raw text so a truncated reply resolves to unresolved instead of aborting.
    return str(response.json()["message"]["content"])


def _arguments(argv=None):
    """Parse full-build bridge options without accepting the obsolete prefix mode.

    Args:
        argv: Optional argument list for tests.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rerank", action="store_true",
                        help="rerank structural survivors with the configured cross-encoder")
    parser.add_argument("--llm", action="store_true",
                        help="ask the tiny local model only for ambiguous top candidates")
    return parser.parse_args(argv)


def main(argv=None):
    """Build every signal and module alignment into ``output/bridge.json``.

    Args:
        argv: Optional argument list for tests.

    Returns:
        None.
    """
    args = _arguments(argv)
    spec_data = _load_json(cfg.SPEC_SIGNALS_PATH)
    rtl_data = _load_json(cfg.RTL_PATH)
    hierarchy_data = _load_json(cfg.SPEC_HIERARCHY_PATH)
    scorer = None
    if args.rerank:
        from retrieve import Reranker

        scorer = Reranker().score_pairs
    fingerprints = source_fingerprints()
    artifact = build_bridge(spec_data, rtl_data, hierarchy_data, scorer=scorer,
                            llm_resolver=ollama_resolver if args.llm else None,
                            source_fingerprints=fingerprints)
    _write_bridge(artifact)
    summary = artifact["summary"]
    print(f"====  wrote {cfg.BRIDGE_PATH.relative_to(cfg.ROOT)}: "
          f"{summary['signal_alignments']} signal alignments, "
          f"{summary['unresolved_signals']} unresolved, "
          f"{summary['module_alignments']} section/module alignments")


if __name__ == "__main__":
    main()
