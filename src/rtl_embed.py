"""Build module-first RTL retrieval records from ``output/rtl.json``.

Only modules and useful compiler-bounded structural blocks are vectorized.  Signal
declarations remain exact-addressable metadata on their owning module instead of becoming
thousands of tiny semantic documents.
"""

import json
import re

import configure as cfg
from embed import VectorStore, build_manifest, write_manifest
from naming import identifier_tokens, normalize_identifier


_MODULE_LINKS = 24
# Rendered signal lists are bounded; the full lists stay in metadata, unembedded.
_CONSTRUCT_SIGNALS = 16
_MODULE_PORTS = 24
_FACET_MIN_CHARS = 80
# Past a dozen phrases the expanded names outweigh the module's own prose, measurably.
_TERMS = 12
_LOOKUP_TERMS = 128
_COMMENT = re.compile(r"//+([^\n]*)|/\*(.*?)\*/", re.S)
_RULE = re.compile(r"^[\s\-=_*#~+.─━—–]*$")
# Box-drawing banners cost about one token per character, so they are collapsed away.
_DECORATION = re.compile(r"[─━—–\-=_*#~+]{3,}")


def _unique(items, limit=None):
    """Deduplicate non-empty values in input order, optionally bounded.

    Args:
        items: Values to deduplicate.
        limit: Maximum values to keep, or None for no bound.

    Returns:
        Unique non-empty values in first-seen order.
    """
    out = []
    for item in items:
        if item not in (None, "") and item not in out:
            out.append(item)
            if limit is not None and len(out) >= limit:
                break
    return out


def _record(chunk_id, record_id, context_header, text, source_type, module, entry,
            lookup_terms, **metadata):
    """Build one vector-compatible retrieval record with shared provenance.

    Args:
        chunk_id: Stable identifier addressing this record in the index.
        record_id: Compiler-derived identifier for the underlying construct.
        context_header: Breadcrumb prepended to the embedded text.
        text: Embedded retrieval surface.
        source_type: ``"module"`` or ``"construct"``.
        module: Owning module name.
        entry: Source entry supplying file and line provenance.
        lookup_terms: Names this record can be found by exactly.
        metadata: Extra record-type fields, and an optional ``line`` override.

    Returns:
        One retrieval record carrying provenance.
    """
    record = {
        "chunk_id": chunk_id,
        "record_id": record_id,
        "source_type": source_type,
        "context_header": context_header,
        "text": text,
        "module": module,
        "file": entry["file"],
        "line": metadata.pop("line"),
        "lookup_terms": _unique(lookup_terms, _LOOKUP_TERMS),
    }
    record.update(metadata)
    return record


def comments(text):
    """Extract useful comment prose, line and block comments, from source text.

    Args:
        text: RTL source text to scan.

    Returns:
        Unique comment bodies in source order, one per comment line, excluding divider
        rules.
    """
    found = []
    for match in _COMMENT.finditer(text):
        lines = [match.group(1)] if match.group(1) is not None else match.group(2).split("\n")
        for line in lines:
            body = _DECORATION.sub(" ", line.strip().lstrip("*")).strip()
            if body and not _RULE.match(body) and body not in found:
                found.append(body)
    return found


def _signal_metadata(entry):
    """Return declarations retained for exact lookup, never as vector rows.

    Args:
        entry: One module record from ``rtl.json``.

    Returns:
        Port, signal, and parameter declarations with their populated fields.
    """
    signals = []
    for field in ("ports", "signals", "parameters"):
        for declaration in entry[field]:
            item = {
                key: declaration[key]
                for key in (
                    "signal_id", "construct_type", "original_name", "normalized_name",
                    "normalized_tokens", "direction", "data_type", "width",
                    "width_expression", "file_path", "start_line", "end_line",
                    "reader_sites", "writer_sites",
                ) if declaration.get(key) is not None
            }
            signals.append(item)
    return signals


def _terms(names):
    """Render identifiers as the plain words a question is likely to use.

    Args:
        names: Source identifier spellings.

    Returns:
        Space-joined expanded token strings, deduplicated and bounded.
    """
    return _unique((" ".join(identifier_tokens(name)) for name in names if name),
                   _TERMS)


def port_connections(rtl):
    """Map every module port to the peer modules bound to the same parent-scope net.

    Naming conventions cannot be trusted to say which stage a port serves, but the
    elaborated netlist can: on one parent-scope net, an ``output`` binding drives every
    other binding, and a non-output binding reads only from ``output`` bindings.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        Module name to ``{"drives": [...], "reads": [...]}`` formal/peer pairs.
    """
    scoped = {}
    for entry in rtl["modules"].values():
        for instance in entry["instances"]:
            target = instance["target_module"]
            for binding in instance["bindings"]:
                for actual in binding["actual_signals"]:
                    scoped.setdefault((binding["parent_module"], actual), []).append(
                        (target, binding["formal_name"], binding["direction"].casefold()))
    links = {}
    for endpoints in scoped.values():
        for target, formal, direction in endpoints:
            # A driver feeds every non-driver on the net; a reader hears only drivers.
            side = "drives" if direction == "output" else "reads"
            peers = [peer for peer, _, peer_direction in endpoints
                     if peer != target and (peer_direction == "output") == (side == "reads")]
            if peers:
                links.setdefault(target, {}).setdefault(side, []).extend(
                    (formal, peer) for peer in peers)
    return links


def _budgeted_facets(parts, max_chars=cfg.RTL_CHUNK_CHARS):
    """Keep a module's facets within the budget, trimming the last one to fit.

    One module stays one chunk: several chunks per module would compete with each
    other for the same retrieval slots, so the surface is bounded instead of split.

    Args:
        parts: ``(label, text)`` facets in priority order.
        max_chars: Character budget for the whole surface.

    Returns:
        Facet strings that together fit the budget, last one trimmed if needed.
    """
    kept, size = [], 0
    for _, text in parts:
        remaining = max_chars - size
        if not kept:
            kept.append(text)
            size = len(text)
            continue
        if len(text) + 1 <= remaining:
            kept.append(text)
            size += len(text) + 1
        elif remaining > _FACET_MIN_CHARS:
            # A long trailing facet is trimmed rather than dropped whole.
            kept.append(text[:remaining - 1].rstrip())
            break
        else:
            break
    return kept


def chunks(rtl):
    """Build one stable, provenance-bearing vector record per module.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        One module record per module, in module-name order.
    """
    parents = {}
    for parent, entry in sorted(rtl["modules"].items()):
        for instance in entry["instances"]:
            parents.setdefault(instance["target_module"], [])
            if parent not in parents[instance["target_module"]]:
                parents[instance["target_module"]].append(parent)
    links = port_connections(rtl)
    records = []
    for name, entry in sorted(rtl["modules"].items()):
        prose = comments(entry["preamble"] + entry["source_code"])
        port_names = [port["original_name"] for port in entry["ports"]]
        kids = _unique(instance["target_module"] for instance in entry["instances"])
        wiring = links.get(name, {})
        drives = _unique((f"{formal} -> {peer}"
                          for formal, peer in wiring.get("drives", [])), _MODULE_LINKS)
        reads = _unique((f"{formal} <- {peer}"
                         for formal, peer in wiring.get("reads", [])), _MODULE_LINKS)
        terms = _terms([name, *port_names])
        said = "; ".join(prose)
        parts = [(label, text) for label, text in (
            ("identity", f"module {name}"),
            ("parent", f"instantiated by: {', '.join(parents[name])}"
             if name in parents else ""),
            ("ports", f"ports: {', '.join(_unique(port_names, _MODULE_PORTS))}"
             if port_names else ""),
            ("terms", f"terms: {', '.join(terms)}" if terms else ""),
            ("drives", f"drives: {', '.join(drives)}" if drives else ""),
            ("reads", f"reads: {', '.join(reads)}" if reads else ""),
            ("instantiates", f"instantiates: {', '.join(kids)}" if kids else ""),
            ("comments", f"comments: {said}" if said else ""),
        ) if text]
        records.append(_record(
            chunk_id=f"rtl:{name}",
            record_id=entry["module_id"],
            context_header=f"RTL > {cfg.RTL_DIR.name}/{name}",
            text="\n".join(_budgeted_facets(parts)),
            source_type="module",
            module=name,
            entry=entry,
            lookup_terms=[name, entry["original_name"], normalize_identifier(name)],
            line=entry["start_line"],
            start_line=entry["start_line"],
            end_line=entry["end_line"],
            original_name=entry["original_name"],
            normalized_name=entry["normalized_name"],
            signal_metadata=_signal_metadata(entry),
        ))
    return records


def _whole_lines(source, max_chars):
    """Trim source to the budget without cutting a line in half.

    Args:
        source: Full source text of one construct.
        max_chars: Character budget for the embedded surface.

    Returns:
        Leading whole lines of the source that fit the budget.
    """
    kept, size = [], 0
    for line in source.split("\n"):
        if kept and size + len(line) + 1 > max_chars:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept)[:max_chars].rstrip()


def _structural_records(rtl):
    """Project only useful module structural blocks into vector records.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        One record per always block, function, task, and assignment group.
    """
    records = []
    fields = ("always_blocks", "functions_tasks", "assignment_groups")
    for module, entry in sorted(rtl["modules"].items()):
        blocks = [block for field in fields for block in entry[field]]
        blocks.sort(key=lambda block: (
            block["start_line"], block["end_line"], block["construct_type"]))
        for block in blocks:
            first, last = block["start_line"], block["end_line"]
            kind = block["construct_type"]
            surface = block["source_code"].strip()
            chunk_id = f"rtl:construct:{module}:{block['kind']}:{first}-{last}"
            reads = block["signals_read"]
            writes = block["signals_written"]
            name = block["original_name"]
            terms = _terms([module, *writes, *reads])
            shown_reads = _unique(reads, _CONSTRUCT_SIGNALS)
            shown_writes = _unique(writes, _CONSTRUCT_SIGNALS)
            heading = [part for part in (
                f"reads: {', '.join(shown_reads)}" if shown_reads else "",
                f"writes: {', '.join(shown_writes)}" if shown_writes else "",
                f"terms: {', '.join(terms)}" if terms else "",
            ) if part]
            # One construct stays one chunk; the surface is bounded, never split.
            budget = max(cfg.RTL_CHUNK_CHARS - len("\n".join(heading)) - 1,
                         cfg.RTL_CHUNK_MIN_SOURCE_CHARS)
            surface = _whole_lines(surface, budget)
            body = "\n".join([*heading, *([surface] if surface else [])])
            records.append(_record(
                chunk_id=chunk_id,
                record_id=block["block_id"],
                context_header=(f"RTL > {cfg.RTL_DIR.name}/{module} > "
                                f"{kind.lower()} lines {first}-{last}"),
                text=body,
                source_type="construct",
                module=module,
                entry=block,
                # Named functions/tasks are exact-addressable; source operands are not.
                lookup_terms=[name] if "@" not in name else [],
                line=first,
                start_line=first,
                end_line=last,
                signals_read=reads,
                signals_written=writes,
            ))
    return records


def retrieval_records(rtl):
    """Return module records first, followed only by useful structural records.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        Module records followed by structural records, in stable order.
    """
    return [*chunks(rtl), *_structural_records(rtl)]


def main():
    """Write retrieval records, embed them, and write the index manifest.

    Returns:
        None.
    """
    if not cfg.RTL_PATH.exists():
        raise SystemExit(f"no {cfg.RTL_PATH.name}; run src/rtl_ingest.py first")
    with open(cfg.RTL_PATH, encoding="utf-8") as source:
        rtl = json.load(source)
    records = retrieval_records(rtl)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(cfg.RTL_CHUNKS_PATH, "w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record) + "\n")
    modules = sum(record["source_type"] == "module" for record in records)
    structures = len(records) - modules
    print(f"{len(records)} RTL records ({modules} modules, {structures} structures) "
          f"-> {cfg.RTL_CHUNKS_PATH.relative_to(cfg.ROOT)}")
    store = VectorStore.build(records)
    store.save(cfg.RTL_INDEX_DIR)
    write_manifest(build_manifest(records), cfg.RTL_MANIFEST_PATH)
    print(f"indexed -> {cfg.RTL_INDEX_DIR.relative_to(cfg.ROOT)}")

if __name__ == "__main__":
    main()
