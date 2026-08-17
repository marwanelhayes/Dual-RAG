"""Route questions through specification RAG, RTL RAG, or an enforced comparison path."""

import json
import re
import sys
from pathlib import Path
from typing import TypedDict

import configure as cfg


SPEC = "SPEC"
RTL = "RTL"
COMPARISON = "COMPARISON"
QUERY_TYPES = frozenset({SPEC, RTL, COMPARISON})


class State(TypedDict, total=False):
    """Describe the inspectable state passed between LangGraph nodes."""

    question: str
    query_type: str
    spec_evidence: list
    spec_signals: list
    bridge_matches: list
    module_matches: list
    rtl_evidence: list
    rtl_blocks: list
    final_context: str
    trace: list
    answer: str


class ArtifactFreshnessError(RuntimeError):
    """Report a source/index manifest mismatch before stale retrieval can run."""


def _read_json(path, default):
    """Read a JSON artifact or return a caller-owned empty value.

    Args:
        path: Artifact path.
        default: Value returned when the path is absent.

    Returns:
        Parsed JSON data or the supplied default.
    """
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_jsonl(path):
    """Read non-empty JSON Lines records from a generated artifact.

    Args:
        path: JSON Lines artifact path.

    Returns:
        Parsed records, or an empty list when the path is absent.
    """
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _append_trace(state, event, **details):
    """Return a new trace containing one structured debugging event.

    Args:
        state: Current graph state.
        event: Stable event label.
        **details: JSON-compatible event details.

    Returns:
        Existing trace entries followed by the new event.
    """
    return [*state.get("trace", []), {"event": event, **details}]


_COMPARISON = re.compile(
    r"\b(?:implement(?:ed|ation|s)?|missing|gap|coverage|comp(?:are|ared|arison)|"
    r"differ(?:s|ed|ence)?|match(?:es|ed)?|contradict(?:s|ory|ed)?|consistent|"
    r"compliant|support(?:s|ed)?\s+"
    r"(?:all|every)|rtl\s+(?:meet|satisf|support)|spec(?:ification)?\s+(?:vs|and)\s+rtl)\b",
    re.IGNORECASE)
_RTL = re.compile(
    r"\b(?:rtl|systemverilog|verilog|module|always_(?:ff|comb|latch)|always\s+block|"
    r"driv(?:e|en|er|es)|readers?|writers?|instance|instantiate|source\s+line|\.sv)\b",
    re.IGNORECASE)
_RTL_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9$]*_[A-Za-z0-9_$]+\b")
# Conventional staging suffixes and pipeline depth; _n means active-low, never next.
_STAGING_SUFFIX = re.compile(
    r"_(?:next|nxt|new|comb|pre|post|reg|ff|sync|d|q|r)\d*$")


def classify_query(question):
    """Classify a question into the only three supported retrieval routes.

    Args:
        question: User question.

    Returns:
        ``SPEC``, ``RTL``, or ``COMPARISON``.
    """
    text = question or ""
    if _COMPARISON.search(text):
        return COMPARISON
    if _RTL.search(text) or _RTL_IDENTIFIER.search(text):
        return RTL
    return SPEC


def _hit_record(hit, domain):
    """Normalize one retriever result into a JSON-compatible evidence record.

    Args:
        hit: LangChain Document or ``(Document, score)`` pair.
        domain: Evidence domain label.

    Returns:
        Evidence record with text, score, identifier, and metadata.
    """
    if isinstance(hit, tuple):
        document, score = hit
    else:
        document, score = hit, None
    metadata = dict(getattr(document, "metadata", {}) or {})
    record_id = (metadata.get("chunk_id") or metadata.get("record_id")
                 or metadata.get("module") or "unknown")
    return {
        "domain": domain,
        "id": str(record_id),
        "score": None if score is None else float(score),
        "text": str(getattr(document, "page_content", document)),
        "metadata": metadata,
    }


def _dedupe_evidence(records):
    """Deduplicate evidence by stable identifier without changing rank order.

    Args:
        records: Evidence records in preferred order.

    Returns:
        First occurrence of every stable evidence identifier.
    """
    seen = set()
    result = []
    for record in records:
        key = (record.get("domain"), record.get("id"))
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def _shingles(text, size):
    """Return the set of word n-grams used to detect verbatim reuse.

    Args:
        text: Evidence text.
        size: Words per n-gram.

    Returns:
        Word n-gram tuples.
    """
    words = re.findall(r"[a-z0-9_.]+", str(text or "").lower())
    return {tuple(words[index:index + size])
            for index in range(max(len(words) - size + 1, 0))}


def drop_near_duplicates(records, size=None, threshold=None):
    """Drop chunks that reproduce an earlier chunk's sentences verbatim.

    Args:
        records: Evidence records in preferred order.
        size: Words per n-gram, defaulting to the configured shingle width.
        threshold: Maximum shared-n-gram fraction of the shorter chunk.

    Returns:
        Highest-ranked record for every distinct passage.
    """
    size = int(getattr(cfg, "AGENT_DEDUP_SHINGLE", 8) if size is None else size)
    threshold = float(getattr(cfg, "AGENT_DEDUP_MAX_OVERLAP", 0.25)
                      if threshold is None else threshold)
    kept = []
    signatures = []
    for record in records:
        current = _shingles(record.get("text", ""), size)
        # Sentence-overlap chunking reuses whole sentences, so shared n-grams are literal.
        if any(current and earlier
               and len(current & earlier) / min(len(current), len(earlier)) >= threshold
               for earlier in signatures):
            continue
        signatures.append(current)
        kept.append(record)
    return kept


def expand_spec_hierarchy(hits, hierarchy, records, limit=2):
    """Add bounded parent, sibling, and child context around retrieved sections.

    Args:
        hits: Ranked specification evidence records.
        hierarchy: Deterministic specification hierarchy artifact.
        records: All specification chunks.
        limit: Maximum neighbouring chunks to add.

    Returns:
        Original hits followed by bounded neighbouring-section chunks.
    """
    hierarchy_sections = list(hierarchy.get("sections", []))
    hierarchy_sections.extend(
        section for document in hierarchy.get("documents", [])
        for section in document.get("sections", []))
    sections = {item.get("node_id") or item.get("section_id"): item
                for item in hierarchy_sections}
    chunks_by_section = {}
    for record in records:
        section_id = record.get("subsection_id") or record.get("section_id")
        if section_id:
            chunks_by_section.setdefault(section_id, []).append(record)
    wanted = []
    for hit in hits:
        metadata = hit.get("metadata", {})
        section_id = metadata.get("subsection_id") or metadata.get("section_id")
        section = sections.get(section_id, {})
        wanted.extend([
            section.get("parent_id"), section.get("prev_id"), section.get("next_id"),
            *(section.get("children_ids") or []),
        ])
    existing = {hit.get("id") for hit in hits}
    neighbours = []
    for section_id in dict.fromkeys(item for item in wanted if item):
        for record in chunks_by_section.get(section_id, [])[:1]:
            if record.get("chunk_id") in existing:
                continue
            neighbours.append({
                "domain": "spec",
                "id": record["chunk_id"],
                "score": None,
                "text": f"{record.get('context_header', '')}\n{record.get('text', '')}",
                "metadata": dict(record),
                "expansion": "section_neighbour",
            })
            if len(neighbours) >= limit:
                return _dedupe_evidence([*hits, *neighbours])
    return _dedupe_evidence([*hits, *neighbours])


def _name_tokens(value):
    """Return simple normalized name tokens without requiring model weights.

    Args:
        value: Identifier or prose surface.

    Returns:
        Lowercase alphanumeric tokens.
    """
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def select_spec_signals(question, evidence, signals, limit=8):
    """Select table-derived spec signals supported by retrieved chunks or sections.

    Args:
        question: User question.
        evidence: Retrieved specification evidence.
        signals: Structured specification signal records.
        limit: Maximum signals retained for bridge lookup.

    Returns:
        Relevant signal records in deterministic score order.
    """
    chunk_ids = {item.get("id") for item in evidence}
    subsection_ids = {item.get("metadata", {}).get("subsection_id")
                      for item in evidence
                      if item.get("metadata", {}).get("subsection_id")}
    root_section_ids = {item.get("metadata", {}).get("section_id")
                        for item in evidence
                        if item.get("metadata", {}).get("section_id")
                        and not item.get("metadata", {}).get("subsection_id")}
    table_ids = {
        table_id for item in evidence
        for table_id in item.get("metadata", {}).get("table_ids", []) if table_id
    }
    query_tokens = _name_tokens(question)
    ranked = []
    for position, signal in enumerate(signals):
        signal_chunks = set(signal.get("chunk_ids") or [signal.get("chunk_id")]) - {None}
        signal_names = [signal.get("name"), signal.get("original_name"),
                        signal.get("normalized_name")]
        overlap = len(query_tokens.intersection(
            token for surface in signal_names for token in _name_tokens(surface)))
        provenance = bool(
            chunk_ids & signal_chunks
            or signal.get("table_id") in table_ids
            or signal.get("subsection_id") in subsection_ids
            or (not signal.get("subsection_id")
                and signal.get("section_id") in root_section_ids))
        if not provenance and not overlap:
            continue
        ranked.append((overlap, int(provenance), -position, signal))
    ranked.sort(reverse=True, key=lambda item: item[:3])
    return [item[-1] for item in ranked[:limit]]


def lookup_bridge(signals, spec_evidence, artifact, limit=12):
    """Find prebuilt signal and section alignments relevant to spec evidence.

    Args:
        signals: Selected specification signal records.
        spec_evidence: Retrieved specification evidence.
        artifact: Cross-domain bridge artifact.
        limit: Maximum signal mappings retained.

    Returns:
        ``(signal_matches, module_matches)``.
    """
    signal_ids = {item.get("signal_id") or item.get("id") for item in signals}
    signal_ids.discard(None)
    names = {str(item.get("name") or item.get("original_name") or "").casefold()
             for item in signals}
    names.discard("")
    section_ids = {
        value for item in spec_evidence for value in (
            item.get("metadata", {}).get("section_id"),
            item.get("metadata", {}).get("subsection_id")) if value
    }
    matches = []
    if signal_ids or names:
        for mapping in [*artifact.get("signal_alignments", []),
                        *artifact.get("unresolved_signals", [])]:
            mapping_id = (mapping.get("spec_signal_id")
                          or mapping.get("spec", {}).get("id"))
            mapping_name = str(
                mapping.get("spec_original_name") or mapping.get("spec_signal")
                or mapping.get("spec_name") or mapping.get("spec", {}).get("name")
                or "").casefold()
            if mapping_id not in signal_ids and mapping_name not in names:
                continue
            matches.append(mapping)
    modules = [mapping for mapping in artifact.get("module_alignments", [])
               if section_ids and (mapping.get("spec_section_id")
                                   or mapping.get("spec", {}).get("section_id"))
               in section_ids]
    return matches[:limit], modules[:limit]


def _mapping_values(mapping, keys):
    """Collect scalar target values from a bridge mapping and its candidates.

    Args:
        mapping: Bridge alignment record.
        keys: Candidate field names.

    Returns:
        Unique non-empty string values.
    """
    values = []
    sources = [mapping, mapping.get("rtl", {}), *(mapping.get("candidates") or [])]
    for source in sources:
        for key in keys:
            value = source.get(key) if isinstance(source, dict) else None
            if value and str(value) not in values:
                values.append(str(value))
    return values


def bridge_targets(signal_matches, module_matches):
    """Return target RTL signals and modules for comparison retrieval.

    Args:
        signal_matches: Relevant signal alignment records.
        module_matches: Relevant section-to-module records.

    Returns:
        ``(signal_names, module_names)`` in stable order.
    """
    signals = []
    modules = []
    for mapping in signal_matches:
        if mapping.get("confidence_level") == "UNRESOLVED":
            candidates = [candidate for candidate in mapping.get("candidates", [])
                          if candidate.get("survived_structural")
                          and candidate.get("score", 0)
                          >= getattr(cfg, "BRIDGE_QUERY_CANDIDATE_MIN", 0.45)]
            for candidate in candidates[:getattr(cfg, "BRIDGE_QUERY_CANDIDATES", 3)]:
                signals.extend(_mapping_values(
                    candidate, ("rtl_original_name", "rtl_signal", "rtl_name")))
                modules.extend(_mapping_values(
                    candidate, ("rtl_module_name", "rtl_module", "module_name")))
            continue
        if not any(mapping.get(key) for key in (
                "rtl_signal_id", "rtl_original_name", "rtl_signal", "rtl_name")):
            continue
        signals.extend(_mapping_values(
            {key: value for key, value in mapping.items() if key != "candidates"},
            ("rtl_original_name", "rtl_signal", "rtl_name", "signal_name")))
        modules.extend(_mapping_values(
            {key: value for key, value in mapping.items() if key != "candidates"},
            ("rtl_module_name", "rtl_module", "module_name", "module")))
    for mapping in module_matches:
        if mapping.get("confidence_level") == "UNRESOLVED":
            candidates = [candidate for candidate in mapping.get("candidates", [])
                          if candidate.get("score", 0)
                          >= getattr(cfg, "BRIDGE_QUERY_CANDIDATE_MIN", 0.45)]
            for candidate in candidates[:getattr(cfg, "BRIDGE_QUERY_CANDIDATES", 3)]:
                modules.extend(_mapping_values(
                    candidate, ("rtl_module_name", "rtl_module", "module_name")))
            continue
        if not any(mapping.get(key) for key in (
                "rtl_module_id", "rtl_module_name", "rtl_module", "module_name")):
            continue
        modules.extend(_mapping_values(
            {key: value for key, value in mapping.items() if key != "candidates"},
            ("rtl_module_name", "rtl_module", "module_name", "module")))
    return list(dict.fromkeys(signals)), list(dict.fromkeys(modules))


def _module_table(rtl):
    """Return the module dictionary from current or minimally compatible RTL data.

    Args:
        rtl: Parsed RTL artifact.

    Returns:
        Module name to module metadata mapping.
    """
    modules = rtl.get("modules", {})
    if isinstance(modules, dict):
        return modules
    return {item.get("original_name") or item.get("name"): item
            for item in modules if item.get("original_name") or item.get("name")}


def _staging_base(name):
    """Return one identifier without a conventional register or pipeline suffix.

    Args:
        name: RTL identifier.

    Returns:
        The casefolded stem shared by every stage of the same value.
    """
    return _STAGING_SUFFIX.sub("", str(name).casefold())


def _staged_names(wanted, module):
    """Expand targets with the module's own registered and next-state partners.

    SystemVerilog spreads one registered value across two identifiers, so the block
    deciding the value usually writes a differently suffixed name than the one asked
    for.  Only identifiers the module itself declares may join a target.

    Args:
        wanted: Casefolded target signal names.
        module: Module metadata from the RTL artifact.

    Returns:
        The targets plus every declared identifier sharing a staging stem.
    """
    declared = {str(item.get("original_name") or item.get("name") or "").casefold()
                for key in ("ports", "signals") for item in module.get(key) or []}
    stems = {_staging_base(name) for name in wanted}
    return wanted | {name for name in declared if name and _staging_base(name) in stems}


def surrounding_rtl_blocks(rtl, signal_names, module_names, limit=6):
    """Select AST blocks that read or write bridge-targeted signals.

    Args:
        rtl: Module-centric Verilator artifact.
        signal_names: Target RTL signal names.
        module_names: Target RTL module names.
        limit: Maximum source blocks retained.

    Returns:
        Ranked block evidence with exact file and line provenance.
    """
    wanted_signals = {name.casefold() for name in signal_names}
    wanted_modules = {name.casefold() for name in module_names}
    ranked = []
    for module_key, module in _module_table(rtl).items():
        module_name = str(module.get("original_name") or module.get("name") or module_key)
        module_hit = module_name.casefold() in wanted_modules
        block_groups = [module.get("always_blocks", []), module.get("functions_tasks", []),
                        module.get("assignment_groups", []), module.get("assignments", []),
                        module.get("structural_chunks", [])]
        staged_signals = _staged_names(wanted_signals, module)
        for group in block_groups:
            for block in group or []:
                reads = block.get("signals_read") or block.get("inputs") or []
                writes = block.get("signals_written") or block.get("outputs") or []
                touched = {str(name).casefold() for name in [*reads, *writes]}
                signal_hits = wanted_signals.intersection(touched)
                staged_hits = staged_signals.intersection(touched) - signal_hits
                if not module_hit and not signal_hits and not staged_hits:
                    continue
                score = 2 * len(signal_hits) + len(staged_hits) + int(module_hit)
                ranked.append((score, module_name, int(block.get("start_line")
                                                       or block.get("first") or 0), block,
                               sorted(signal_hits | staged_hits)))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    records = []
    for _, module_name, _, block, matched in ranked[:limit]:
        records.append({
            "domain": "rtl",
            "id": block.get("construct_id") or block.get("record_id")
                  or f"{module_name}:{block.get('start_line', block.get('first', 0))}",
            "module": module_name,
            "file": block.get("file") or block.get("file_path"),
            "start_line": block.get("start_line") or block.get("first"),
            "end_line": block.get("end_line") or block.get("last"),
            "signals_read": block.get("signals_read") or block.get("inputs") or [],
            "signals_written": block.get("signals_written") or block.get("outputs") or [],
            "matched_signals": matched,
            "text": (block.get("source_code") or block.get("source_text")
                     or block.get("text") or ""),
            "metadata": dict(block),
        })
    return records


def _bounded(text, limit):
    """Truncate evidence at a deterministic character boundary.

    Args:
        text: Evidence text.
        limit: Maximum characters.

    Returns:
        Original or visibly truncated text.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 15)].rstrip() + "\n[TRUNCATED]"


def _spec_context(evidence, signals):
    """Render bounded, citable specification evidence.

    Args:
        evidence: Specification evidence records.
        signals: Relevant structured spec signals.

    Returns:
        Human-readable evidence text.
    """
    parts = []
    for item in evidence:
        metadata = item.get("metadata", {})
        page = metadata.get("page") or metadata.get("start_page") or "?"
        parts.append(f"[spec:{item['id']} p.{page}]\n{item.get('text', '')}")
    if signals:
        rows = [
            f"- {item.get('name') or item.get('original_name')}"
            f" width={item.get('width')} direction={item.get('direction')}"
            f" table={item.get('table_id')}"
            for item in signals
        ]
        parts.append("STRUCTURED SPEC SIGNALS:\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _alignment_site(mapping):
    """Render the RTL file and line an alignment points at.

    Args:
        mapping: Bridge alignment record.

    Returns:
        A citable source suffix, or an empty string when the record has no RTL side.
    """
    rtl = (mapping.get("provenance") or {}).get("rtl") or {}
    file = rtl.get("file")
    return f" at {Path(str(file)).name}:{rtl.get('start_line')}" if file else ""


def _evidence_detail(mapping):
    """Render the falsifiable structural components behind one alignment.

    Args:
        mapping: Bridge alignment record.

    Returns:
        A bracketed width and direction comparison, or an empty string.
    """
    top = next(iter(mapping.get("candidates") or []), {})
    components = mapping.get("component_evidence") or top.get("component_evidence") or {}
    parts = [f"{name} {components[name].get('detail')}"
             for name in ("width", "direction")
             if isinstance(components.get(name), dict) and components[name].get("available")]
    return f" [{'; '.join(parts)}]" if parts else ""


def _bridge_context(signal_matches, module_matches):
    """Render cross-domain alignment evidence without behavioral conclusions.

    Args:
        signal_matches: Signal alignment records.
        module_matches: Section-to-module alignment records.

    Returns:
        Compact alignment text.
    """
    rows = []
    for item in signal_matches:
        spec_name = item.get("spec_original_name") or item.get("spec_signal") \
            or item.get("spec_name") \
            or item.get("spec", {}).get("name")
        unresolved = item.get("confidence_level") == "UNRESOLVED"
        rtl_names = [] if unresolved else _mapping_values(
            {key: value for key, value in item.items() if key != "candidates"},
            ("rtl_original_name", "rtl_signal", "rtl_name", "signal_name",
             "original_name"))
        candidate_names = _mapping_values(
            {"candidates": item.get("candidates", [])},
            ("rtl_original_name", "rtl_signal", "rtl_name", "signal_name"))
        candidate_suffix = (
            f" candidates={', '.join(candidate_names[:3])}" if unresolved and candidate_names
            else "")
        reason_suffix = f" reason={item['reason']}" if unresolved and item.get("reason") else ""
        rows.append(
            f"signal {spec_name} -> {', '.join(rtl_names) or 'UNRESOLVED'} "
            f"identity_match={item.get('confidence_level', item.get('status', 'UNRESOLVED'))} "
            f"identity_score={item.get('confidence', 0)}{_alignment_site(item)}"
            f"{_evidence_detail(item)}{reason_suffix}{candidate_suffix}")
    for item in module_matches:
        spec_title = item.get("spec_section_title") or item.get("spec_section") \
            or item.get("spec_title") \
            or item.get("spec", {}).get("title")
        unresolved = item.get("confidence_level") == "UNRESOLVED"
        rtl_names = [] if unresolved else _mapping_values(
            {key: value for key, value in item.items() if key != "candidates"},
            ("rtl_module_name", "rtl_module", "module_name", "module"))
        rows.append(
            f"section {spec_title} -> {', '.join(rtl_names) or 'UNRESOLVED'} "
            f"identity_match={item.get('confidence_level', item.get('status', 'UNRESOLVED'))} "
            f"identity_score={item.get('confidence', 0)}")
    return "\n".join(rows)


def _excerpt_block(text, names):
    """Reduce an oversized block to the lines naming a target, with local context.

    A large state machine would otherwise consume the whole RTL lane and push the
    deciding lines out of the evidence, so only the neighbourhood of each target is
    kept and every elision is marked.

    Args:
        text: Full block source.
        names: Casefolded target signal names matched by this block.

    Returns:
        The original text when it is small, otherwise a marked excerpt.
    """
    limit = int(getattr(cfg, "AGENT_RTL_BLOCK_CHARS", 1200))
    if len(text) <= limit or not names:
        return text
    span = int(getattr(cfg, "AGENT_RTL_BLOCK_CONTEXT_LINES", 2))
    lines = text.splitlines()
    keep = set()
    for index, line in enumerate(lines):
        folded = line.casefold()
        if any(name in folded for name in names):
            keep.update(range(max(0, index - span), min(len(lines), index + span + 1)))
    if not keep:
        return text
    excerpt = []
    previous = None
    for index in sorted(keep):
        if previous is not None and index > previous + 1:
            excerpt.append("[...]")
        excerpt.append(lines[index])
        previous = index
    if keep and min(keep) > 0:
        excerpt.insert(0, "[...]")
    if keep and max(keep) < len(lines) - 1:
        excerpt.append("[...]")
    return "\n".join(excerpt)


def _rtl_context(evidence, blocks):
    """Render retrieved RTL records and exact AST block surroundings.

    Args:
        evidence: RTL retrieval records.
        blocks: Targeted AST block records.

    Blocks are rendered first because they are targeted by exact signal identity,
    so a bounded lane keeps them ahead of broader vector hits.

    Returns:
        Human-readable RTL evidence text.
    """
    parts = []
    for block in blocks:
        file = Path(str(block.get("file") or "rtl")).name
        first = block.get("start_line") or "?"
        last = block.get("end_line") or first
        parts.append(
            f"[rtl:{file}:{first}-{last}] reads={block.get('signals_read', [])} "
            f"writes={block.get('signals_written', [])}\n"
            f"{_excerpt_block(block.get('text', ''), block.get('matched_signals') or [])}")
    for item in evidence:
        metadata = item.get("metadata", {})
        file = metadata.get("file") or metadata.get("file_path") or "rtl"
        line = metadata.get("start_line") or metadata.get("line") or "?"
        parts.append(f"[rtl:{Path(str(file)).name}:{line}]\n{item.get('text', '')}")
    return "\n\n".join(parts)


def merge_evidence(state):
    """Build the only context that the local answer model is allowed to see.

    Args:
        state: Current graph state.

    Returns:
        Bounded final context string.
    """
    total_limit = int(getattr(cfg, "AGENT_CONTEXT_CHARS", 12500))
    has_spec = bool(state.get("spec_evidence"))
    has_rtl = bool(state.get("rtl_evidence") or state.get("rtl_blocks"))
    has_bridge = bool(state.get("bridge_matches") or state.get("module_matches"))
    active = int(has_spec) + int(has_rtl) + int(has_bridge)
    overhead = min(350, max(100, total_limit // 4))
    available = max(total_limit - overhead, active * 48)
    if has_spec and has_rtl:
        spec_share, rtl_share = 0.40, 0.45
    else:
        spec_share, rtl_share = 0.85, 0.85
    bridge_share = max(0.10, 1.0 - spec_share - rtl_share)
    spec_limit = min(int(getattr(cfg, "AGENT_SPEC_CONTEXT_CHARS", 4500)),
                     max(48, int(available * spec_share)))
    rtl_limit = min(int(getattr(cfg, "AGENT_RTL_CONTEXT_CHARS", 5500)),
                    max(48, int(available * rtl_share)))
    bridge_limit = min(int(getattr(cfg, "AGENT_BRIDGE_CONTEXT_CHARS", 2500)),
                       max(48, int(available * bridge_share)))
    sections = [f"QUESTION:\n{state['question']}", f"QUERY TYPE:\n{state['query_type']}"]
    if state.get("spec_evidence"):
        sections.append("SPEC EVIDENCE:\n" + _bounded(
            _spec_context(state["spec_evidence"], state.get("spec_signals", [])),
            spec_limit))
    if state.get("bridge_matches") or state.get("module_matches"):
        sections.append("CROSS-DOMAIN ALIGNMENTS:\n" + _bounded(
            _bridge_context(state.get("bridge_matches", []),
                            state.get("module_matches", [])), bridge_limit))
    if state.get("rtl_evidence") or state.get("rtl_blocks"):
        sections.append("RTL EVIDENCE:\n" + _bounded(
            _rtl_context(state.get("rtl_evidence", []), state.get("rtl_blocks", [])),
            rtl_limit))
    return _bounded("\n\n".join(sections), total_limit)


def local_answer(prompt):
    """Ask the configured local Ollama model to reason over bounded evidence.

    Args:
        prompt: Complete evidence prompt.

    Returns:
        Model answer text.
    """
    import httpx

    host = str(getattr(cfg, "LOCAL_LLM_HOST", "http://localhost:11434")).rstrip("/")
    model = getattr(cfg, "LOCAL_LLM_MODEL", getattr(cfg, "AGENT_MODEL", "qwen3:4b"))
    response = httpx.post(
        f"{host}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": (
                    "Use only supplied evidence. Cite spec chunk/page and RTL file/line. "
                    "An alignment says where to inspect, never that behavior is correct. "
                    "For comparison answer with implemented, partially implemented, missing, "
                    "contradictory, or insufficient evidence, and explain the cited evidence.")},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0, "num_ctx": getattr(cfg, "AGENT_NUM_CTX", 4096)},
        },
        timeout=getattr(cfg, "AGENT_TIMEOUT_S", 300),
    )
    response.raise_for_status()
    return str(response.json().get("message", {}).get("content", "")).strip()


class Runtime:
    """Hold lazy retrieval adapters and generated artifacts for one graph instance."""

    def __init__(self, spec_retriever=None, rtl_retriever=None, answer_model=None,
                 spec_records=None, hierarchy=None, spec_signals=None, rtl=None,
                 bridge=None):
        """Create a runtime with optional hermetic test adapters.

        Args:
            spec_retriever: Optional spec search adapter or callable.
            rtl_retriever: Optional RTL search adapter or callable.
            answer_model: Optional bounded-context answer callable.
            spec_records: Optional in-memory specification chunks.
            hierarchy: Optional in-memory spec hierarchy.
            spec_signals: Optional in-memory structured spec signals.
            rtl: Optional in-memory RTL artifact.
            bridge: Optional in-memory bridge artifact.

        Returns:
            None.
        """
        self._spec_retriever = spec_retriever
        self._rtl_retriever = rtl_retriever
        self.answer_model = answer_model or local_answer
        self.spec_records = spec_records
        self.hierarchy = hierarchy
        self.spec_signals = spec_signals
        self.rtl = rtl
        self.bridge = bridge

    def get_spec_records(self):
        """Return cached specification chunks."""
        if self.spec_records is None:
            self.spec_records = _read_jsonl(cfg.CHUNKS_PATH)
        return self.spec_records

    def get_hierarchy(self):
        """Return cached deterministic specification hierarchy."""
        if self.hierarchy is None:
            path = getattr(cfg, "SPEC_HIERARCHY_PATH", cfg.OUTPUT_DIR / "spec_hierarchy.json")
            self.hierarchy = _read_json(path, {"documents": [], "sections": []})
        return self.hierarchy

    def get_spec_signals(self):
        """Return cached table-derived specification signal records."""
        if self.spec_signals is None:
            path = getattr(cfg, "SPEC_SIGNALS_PATH", cfg.OUTPUT_DIR / "spec_signals.json")
            data = _read_json(path, {"signals": []})
            self.spec_signals = data.get("signals", data if isinstance(data, list) else [])
        return self.spec_signals

    def get_rtl(self):
        """Return cached module-centric Verilator artifact."""
        if self.rtl is None:
            self.rtl = _read_json(cfg.RTL_PATH, {"modules": {}, "warnings": []})
        return self.rtl

    def get_bridge(self):
        """Return cached cross-domain alignment artifact."""
        if self.bridge is None:
            import bridge

            self.bridge = bridge.load_bridge(cfg.BRIDGE_PATH)
            current = bridge.source_fingerprints()
            if self.bridge.get("source_fingerprints") != current:
                raise ArtifactFreshnessError(
                    "cross-domain bridge is stale; run python src/bridge.py")
        return self.bridge

    def spec_search(self, query, k):
        """Search the specification RAG through the configured adapter.

        Args:
            query: User question.
            k: Maximum retrieval results.

        Returns:
            Retriever result sequence.
        """
        if self._spec_retriever is None:
            import rtl_embed
            from retrieve import Retriever

            records = self.get_spec_records()
            status = rtl_embed.index_manifest_status(
                records, cfg.INDEX_DIR, cfg.MANIFEST_PATH)
            if not status["fresh"]:
                raise ArtifactFreshnessError(
                    f"spec index is stale ({status['reason']}); run python src/embed.py")
            self._spec_retriever = Retriever.load(records=records)
        adapter = self._spec_retriever
        return adapter(query, k) if callable(adapter) else adapter.search(query, top_k=k)

    def rtl_search(self, query, k):
        """Search the RTL RAG through the configured adapter.

        Args:
            query: User or bridge-expanded query.
            k: Maximum retrieval results.

        Returns:
            Retriever result sequence.
        """
        if self._rtl_retriever is None:
            import rtl_embed

            records = rtl_embed.retrieval_records(self.get_rtl())
            status = rtl_embed.index_manifest_status(
                records, cfg.RTL_INDEX_DIR, cfg.RTL_MANIFEST_PATH)
            if not status["fresh"]:
                raise ArtifactFreshnessError(
                    f"RTL index is stale ({status['reason']}); run python src/rtl_embed.py")
            self._rtl_retriever = rtl_embed.RTLRetriever(records)
        adapter = self._rtl_retriever
        if callable(adapter):
            try:
                return adapter(query, k=k, rtl=self.get_rtl())
            except TypeError:
                return adapter(query, k=k)
        return adapter.search(query, top_k=k)


def build_graph(spec_retriever=None, rtl_retriever=None, answer_model=None,
                spec_records=None, hierarchy=None, spec_signals=None, rtl=None,
                bridge=None):
    """Compile the three-route LangGraph with structurally mandatory evidence lanes.

    Args:
        spec_retriever: Optional specification retrieval adapter.
        rtl_retriever: Optional RTL retrieval adapter.
        answer_model: Optional answer callable for tests or alternate local models.
        spec_records: Optional in-memory specification records.
        hierarchy: Optional in-memory hierarchy.
        spec_signals: Optional in-memory structured spec signals.
        rtl: Optional in-memory RTL representation.
        bridge: Optional in-memory bridge artifact.

    Returns:
        Compiled LangGraph application.
    """
    from langgraph.graph import END, START, StateGraph

    runtime = Runtime(spec_retriever, rtl_retriever, answer_model, spec_records,
                      hierarchy, spec_signals, rtl, bridge)

    def classify_node(state):
        """Attach the deterministic three-way route to graph state."""
        query_type = classify_query(state["question"])
        return {"query_type": query_type,
                "trace": _append_trace(state, "route", query_type=query_type)}

    def spec_node(state):
        """Retrieve specification evidence and bounded hierarchy neighbours."""
        wanted = getattr(cfg, "AGENT_SEARCH_K", 3)
        # Over-fetch so dropping duplicated passages reallocates slots instead of losing them.
        raw = runtime.spec_search(
            state["question"], wanted * int(getattr(cfg, "AGENT_SPEC_OVERFETCH", 3)))
        hits = drop_near_duplicates(
            [_hit_record(item, "spec") for item in raw])[:wanted]
        expanded = expand_spec_hierarchy(
            hits, runtime.get_hierarchy(), runtime.get_spec_records())
        signals = select_spec_signals(
            state["question"], expanded, runtime.get_spec_signals())
        return {
            "spec_evidence": expanded,
            "spec_signals": signals,
            "trace": _append_trace(
                state, "spec_retrieval", chunks=len(expanded),
                chunk_ids=[item["id"] for item in expanded],
                pages=[item.get("metadata", {}).get("page")
                       or item.get("metadata", {}).get("start_page")
                       for item in expanded],
                sections=list(dict.fromkeys(
                    item.get("metadata", {}).get("subsection_id")
                    or item.get("metadata", {}).get("section_id")
                    for item in expanded
                    if (item.get("metadata", {}).get("subsection_id")
                        or item.get("metadata", {}).get("section_id")))),
                signals=[item.get("name") or item.get("original_name")
                         for item in signals]),
        }

    def bridge_node(state):
        """Look up alignments before comparison RTL retrieval."""
        matches, modules = lookup_bridge(
            state.get("spec_signals", []), state.get("spec_evidence", []),
            runtime.get_bridge())
        return {
            "bridge_matches": matches,
            "module_matches": modules,
            "trace": _append_trace(
                state, "bridge_lookup", signal_matches=len(matches),
                module_matches=len(modules),
                mappings=[{
                    "spec": item.get("spec_original_name") or item.get("spec_signal")
                            or item.get("spec_name")
                            or item.get("spec", {}).get("name"),
                    "rtl": _mapping_values(
                        {key: value for key, value in item.items()
                         if key != "candidates"},
                        ("rtl_original_name", "rtl_signal", "rtl_name", "signal_name",
                         "original_name")),
                    "confidence": item.get("confidence"),
                    "confidence_level": item.get("confidence_level"),
                    "candidates": [candidate.get("rtl_original_name")
                                   for candidate in item.get("candidates", [])
                                   if candidate.get("survived_structural")][:
                                       getattr(cfg, "BRIDGE_QUERY_CANDIDATES", 3)],
                } for item in matches]),
        }

    def rtl_node(state):
        """Retrieve RTL evidence, targeted by bridge outputs for comparisons."""
        signal_names, module_names = bridge_targets(
            state.get("bridge_matches", []), state.get("module_matches", []))
        raw = runtime.rtl_search(state["question"], getattr(cfg, "AGENT_SEARCH_K", 3))
        hits = [_hit_record(item, "rtl") for item in raw]
        blocks = surrounding_rtl_blocks(
            runtime.get_rtl(), signal_names, module_names,
            limit=getattr(cfg, "AGENT_RTL_BLOCKS", 6))
        return {
            "rtl_evidence": _dedupe_evidence(hits),
            "rtl_blocks": blocks,
            "trace": _append_trace(
                state, "rtl_retrieval", records=len(hits),
                record_ids=[item["id"] for item in hits],
                modules=list(dict.fromkeys([
                    *module_names,
                    *(item.get("metadata", {}).get("module") for item in hits
                      if item.get("metadata", {}).get("module")),
                ])),
                blocks=[{
                    "id": item["id"], "file": item.get("file"),
                    "start_line": item.get("start_line"),
                    "end_line": item.get("end_line"),
                } for item in blocks]),
        }

    def merge_node(state):
        """Merge evidence under hard character budgets."""
        context = merge_evidence(state)
        return {"final_context": context,
                "trace": _append_trace(
                    state, "evidence_merge", characters=len(context),
                    limit=int(getattr(cfg, "AGENT_CONTEXT_CHARS", 12500)),
                    truncated="[TRUNCATED]" in context)}

    def answer_node(state):
        """Reason only after every route-required evidence lane has completed."""
        missing = []
        if state["query_type"] in {SPEC, COMPARISON} and not state.get("spec_evidence"):
            missing.append("specification")
        if state["query_type"] in {RTL, COMPARISON} and not (
                state.get("rtl_evidence") or state.get("rtl_blocks")):
            missing.append("RTL")
        if missing:
            answer = "INSUFFICIENT EVIDENCE: missing " + " and ".join(missing) + " evidence."
            called = False
        else:
            answer = runtime.answer_model(state["final_context"])
            called = True
        return {"answer": answer,
                "trace": _append_trace(state, "answer", model_called=called)}

    graph = StateGraph(State)
    graph.add_node("classify", classify_node)
    graph.add_node("retrieve_spec", spec_node)
    graph.add_node("lookup_bridge", bridge_node)
    graph.add_node("retrieve_rtl", rtl_node)
    graph.add_node("merge", merge_node)
    graph.add_node("answer", answer_node)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", lambda state: state["query_type"], {
        SPEC: "retrieve_spec", RTL: "retrieve_rtl", COMPARISON: "retrieve_spec",
    })
    graph.add_conditional_edges("retrieve_spec", lambda state: state["query_type"], {
        SPEC: "merge", COMPARISON: "lookup_bridge",
    })
    graph.add_edge("lookup_bridge", "retrieve_rtl")
    graph.add_edge("retrieve_rtl", "merge")
    graph.add_edge("merge", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def run(app, question):
    """Run one question and return the complete inspectable graph state.

    Args:
        app: Compiled graph from ``build_graph``.
        question: User question.

    Returns:
        Final graph state.
    """
    return app.invoke({"question": question, "trace": []})


def ask(app, question, thread="default"):
    """Run one question and return only its answer for compatibility.

    Args:
        app: Compiled graph from ``build_graph``.
        question: User question.
        thread: Retained compatibility label; no cross-question state is stored.

    Returns:
        Final answer text.
    """
    del thread
    return run(app, question)["answer"]


def trace_report(state):
    """Render the structured route and retrieval trace for a developer.

    Args:
        state: Final graph state.

    Returns:
        Multi-line JSON report.
    """
    return json.dumps({"query_type": state.get("query_type"),
                       "trace": state.get("trace", []),
                       "final_context": state.get("final_context", "")},
                      indent=2, ensure_ascii=False)


def main():
    """Answer a command-line question and print its inspectable retrieval trace.

    Returns:
        None.
    """
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        raise SystemExit('usage: python src/agent.py "question"')
    state = run(build_graph(), question)
    print(trace_report(state), file=sys.stderr)
    print(state["answer"])


if __name__ == "__main__":
    main()
