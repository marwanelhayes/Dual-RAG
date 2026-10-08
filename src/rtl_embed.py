"""Build module-first RTL retrieval records from ``output/rtl.json``.

Only modules and useful compiler-bounded structural blocks are vectorized.  Signal
declarations remain exact-addressable metadata on their owning module instead of becoming
thousands of tiny semantic documents.
"""

import json
import re

import configure as cfg
from embed import VectorStore, build_manifest, write_manifest
from naming import identifier_tokens


_COMMENT = re.compile(r"//+([^\n]*)|/\*(.*?)\*/", re.S)
_RULE = re.compile(r"^[\s\-=_*#~+.─━—–]*$")
# Box-drawing banners cost about one token per character, so they are collapsed away.
_DECORATION = re.compile(r"[─━—–\-=_*#~+]{3,}")


def _unique(items):
    """Deduplicate non-empty values in input order.

    Args:
        items: Values to deduplicate.

    Returns:
        Unique non-empty values in first-seen order.
    """
    out = []
    for item in items:
        if item not in (None, "") and item not in out:
            out.append(item)
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
        "lookup_terms": lookup_terms,
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


def _signal_names(entry):
    """Return the declared port, signal, and parameter names exact lookup matches.

    Args:
        entry: One module record from ``rtl.json``.

    Returns:
        Declaration names as the source spells them.
    """
    return [declaration["original_name"]
            for field in ("ports", "signals", "parameters") for declaration in entry[field]]


def _terms(names):
    """Render identifiers as the plain words a question is likely to use.

    Args:
        names: Source identifier spellings.

    Returns:
        Space-joined expanded token strings, deduplicated.
    """
    return _unique(" ".join(identifier_tokens(name)) for name in names if name)


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


def _facet_lines(label, items, separator, max_chars):
    """Render one labelled list as lines that each fit a window.

    Args:
        label: Facet label, e.g. ``"ports"``.
        items: Facet values in order.
        separator: Joiner between values.
        max_chars: Character budget for one line.

    Returns:
        ``"label: a, b"`` lines; the label repeats on each continuation line.
    """
    lines, current = [], []
    for item in items:
        if current and len(f"{label}: " + separator.join([*current, item])) > max_chars:
            lines.append(f"{label}: " + separator.join(current))
            current = []
        current.append(item)
    if current:
        lines.append(f"{label}: " + separator.join(current))
    return lines


def chunks(rtl):
    """Build one stable, provenance-bearing vector record per module.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        One module record per module, each followed by its continuation windows, in
        module-name order.
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
        facets = (
            ("instantiated by", parents.get(name, []), ", "),
            ("ports", _unique(port_names), ", "),
            ("terms", _terms([name, *port_names]), ", "),
            ("drives", _unique(f"{formal} -> {peer}"
                               for formal, peer in wiring.get("drives", [])), ", "),
            ("reads", _unique(f"{formal} <- {peer}"
                              for formal, peer in wiring.get("reads", [])), ", "),
            ("instantiates", kids, ", "),
            ("comments", prose, "; "),
        )
        lines = [f"module {name}", *(line for label, items, separator in facets
                                      for line in _facet_lines(label, items, separator,
                                                               cfg.RTL_CHUNK_CHARS))]
        records.extend(_windowed(_record(
            chunk_id=f"rtl:{name}",
            record_id=entry["module_id"],
            context_header=f"RTL > {cfg.RTL_DIR.name}/{name}",
            text="",
            source_type="module",
            module=name,
            entry=entry,
            lookup_terms=[name],
            line=entry["start_line"],
            start_line=entry["start_line"],
            end_line=entry["end_line"],
            normalized_name=entry["normalized_name"],
            signal_names=_signal_names(entry),
        ), lines))
    return records


def _windows(lines, max_chars):
    """Pack lines into texts that each fit the budget, cutting only between lines.

    Args:
        lines: Text lines in order.
        max_chars: Character budget for one window.

    Returns:
        Window texts in order; a single line longer than the budget is split into
        budget-sized pieces rather than dropped.
    """
    pieces = [line[start:start + max_chars]
              for line in lines for start in range(0, max(len(line), 1), max_chars)]
    windows, current = [], []
    for piece in pieces:
        if current and len("\n".join([*current, piece])) > max_chars:
            windows.append("\n".join(current))
            current = []
        current.append(piece)
    if current:
        windows.append("\n".join(current))
    return windows


def _windowed(record, lines):
    """Split one record's text lines into windows; the first keeps the record's id.

    Continuation windows ``<chunk_id>:wN`` carry provenance only, so exact lookup and
    signal fills still see each record once.

    Args:
        record: Retrieval record whose ``text`` is replaced by its first window.
        lines: The record's full text lines.

    Returns:
        The record followed by its continuation windows.
    """
    texts = _windows(lines, cfg.RTL_CHUNK_CHARS)
    record["text"] = texts[0]
    provenance = ("record_id", "source_type", "context_header", "module", "file", "line",
                  "start_line", "end_line")
    return [record, *({"chunk_id": f"{record['chunk_id']}:w{index}", "text": text,
                       "lookup_terms": [], **{key: record[key] for key in provenance}}
                      for index, text in enumerate(texts[1:], start=1))]


def _structural_records(rtl):
    """Project only useful module structural blocks into vector records.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        One record per always block, function, task, and assignment group, each
        followed by its continuation windows.
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
            heading = [part for part in (
                f"reads: {', '.join(_unique(reads))}" if reads else "",
                f"writes: {', '.join(_unique(writes))}" if writes else "",
                f"terms: {', '.join(terms)}" if terms else "",
            ) if part]
            lines = [*heading, *(surface.split("\n") if surface else [])]
            records.extend(_windowed(_record(
                chunk_id=chunk_id,
                record_id=block["block_id"],
                context_header=(f"RTL > {cfg.RTL_DIR.name}/{module} > "
                                f"{kind.lower()} lines {first}-{last}"),
                text="",
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
            ), lines))
    return records


def retrieval_records(rtl):
    """Return module records first, followed only by useful structural records.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        Module records followed by structural records, in stable order.
    """
    return [*chunks(rtl), *_structural_records(rtl)]


def over_encoder_window(records, count_tokens):
    """Return the records whose embedded text would be truncated by the encoder.

    Args:
        records: Retrieval records with ``context_header`` and ``text``.
        count_tokens: Encoder-token counter for one text.

    Returns:
        Chunk ids whose header plus text, with two special tokens, exceed
        ``cfg.ENCODER_MAX_TOKENS``.
    """
    return [record["chunk_id"] for record in records
            if count_tokens(record["context_header"] + "\n\n" + record["text"]) + 2
            > cfg.ENCODER_MAX_TOKENS]


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
    # A character budget tuned on one design can overrun the encoder on denser RTL.
    from ingest import count_tokens
    over = over_encoder_window(records, count_tokens)
    if over:
        raise SystemExit(f"{len(over)} RTL chunks over the encoder window {over[:5]}; "
                         "lower RTL_CHUNK_CHARS")
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
