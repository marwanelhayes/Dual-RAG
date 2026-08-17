"""Build module-first RTL retrieval records from ``output/rtl.json``.

Only modules and useful compiler-bounded structural blocks are vectorized.  Signal
declarations remain exact-addressable metadata on their owning module instead of becoming
thousands of tiny semantic documents.
"""

import json
from pathlib import Path
import re
import sys

import configure as cfg
from embed import (Embedder, VectorStore, build_manifest, content_hash, read_manifest,
                   record_to_document, write_manifest)
from naming import identifier_tokens, normalize_identifier
from retrieve import Retriever as HybridRetriever


STRUCTURAL_SURFACE_CHARS = 1200
_MODULE_STRUCTURES = 24
_LOOKUP_TERMS = 128
_COMMENT = re.compile(r"//+(.*)$")
_RULE = re.compile(r"^[\s\-=_*#~+.─━—–]*$")
_IDENTIFIER = re.compile(
    r"0[xX][0-9A-Fa-f]+|[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z0-9_$]+)*"
)
_QUERY_STOP_WORDS = frozenset({
    "the", "and", "for", "from", "into", "what", "where", "which", "who",
    "does", "how", "this", "that", "with", "begin", "end", "else", "case",
    "module", "input", "output", "wire", "logic", "assign", "always", "initial",
    "function", "task", "return", "when", "then", "transfer",
})


def _unique(items, limit=None):
    """Deduplicate non-empty values in input order, optionally bounded."""
    out = []
    for item in items:
        if item not in (None, "") and item not in out:
            out.append(item)
            if limit is not None and len(out) >= limit:
                break
    return out


def _source_id(path):
    """Return a machine-independent source identifier for an RTL file."""
    source = Path(path)
    try:
        relative = source.resolve().relative_to(Path(cfg.RTL_DIR).resolve()).as_posix()
    except (OSError, ValueError):
        relative = source.name
    return f"rtl::file::{relative}"


def _record(chunk_id, record_id, context_header, text, source_type, module, entry,
            lookup_terms, **metadata):
    """Build one vector-compatible retrieval record with shared provenance."""
    record = {
        "chunk_id": chunk_id,
        "record_id": record_id,
        "source_id": _source_id(entry["file"]),
        "domain": "rtl",
        "source_type": source_type,
        "context_header": context_header,
        "text": text,
        "content_hash": content_hash(context_header, text),
        "module": module,
        "file": entry["file"],
        "line": metadata.pop("line", entry.get("start_line", entry.get("line", 1))),
        "lookup_terms": _unique(lookup_terms, _LOOKUP_TERMS),
    }
    record.update(metadata)
    return record


def comments(text):
    """Extract useful line-comment prose from source text."""
    found = []
    for line in text.splitlines():
        match = _COMMENT.search(line)
        if not match:
            continue
        body = match.group(1).strip()
        if body and not _RULE.match(body) and body not in found:
            found.append(body)
    return found


def children(tree):
    """Map each source module to child modules from the pre-order instance tree."""
    parents, out = {}, {}
    for node in tree:
        depth, module = node["depth"], node["module"]
        parents[depth] = module
        parent = parents.get(depth - 1)
        if depth > 0 and parent is not None:
            out.setdefault(parent, [])
            if module not in out[parent]:
                out[parent].append(module)
    return out


def _signal_metadata(entry):
    """Return declarations retained for exact lookup, never as vector rows."""
    signals = []
    for field in ("ports", "signals", "parameters"):
        for declaration in entry.get(field, []):
            item = {
                key: declaration[key]
                for key in (
                    "signal_id", "construct_type", "original_name", "normalized_name",
                    "normalized_tokens", "direction", "data_type", "width",
                    "width_expression", "file_path", "start_line", "end_line",
                    "reader_sites", "writer_sites", "content_hash",
                ) if declaration.get(key) is not None
            }
            # Accept a minimally migrated artifact while keeping the new artifact canonical.
            item.setdefault("original_name", declaration.get("symbol_name")
                            or declaration.get("name"))
            if item["original_name"]:
                item.setdefault("normalized_name", normalize_identifier(item["original_name"]))
                signals.append(item)
    return signals


def chunks(rtl, children):
    """Build one stable, provenance-bearing vector record per module."""
    child_modules = children
    parents = {child: parent for parent, kids in child_modules.items() for child in kids}
    records = []
    for name, entry in sorted(rtl.get("modules", {}).items()):
        source_code = entry.get("source_code", "")
        prose = comments(source_code)
        if not source_code:
            try:
                source_code = Path(entry["file"]).read_text(encoding="utf-8", errors="replace")
                prose = comments(source_code)
            except OSError:
                prose = []
        port_names = [port.get("original_name") or port.get("symbol_name") or port.get("name")
                      for port in entry.get("ports", [])]
        instances = [instance.get("target_module") for instance in entry.get("instances", [])]
        instance_children = _unique(instances)
        kids = _unique([*child_modules.get(name, []), *instance_children])
        structures = [
            *entry.get("always_blocks", []),
            *entry.get("functions_tasks", []),
            *entry.get("assignment_groups", []),
        ]
        structural_summary = ", ".join(
            f"{block.get('construct_type', block.get('kind', 'block')).lower()} "
            f"lines {block.get('start_line')}-{block.get('end_line')}"
            for block in structures[:_MODULE_STRUCTURES]
        )
        said = "; ".join(prose)[:cfg.RTL_CHUNK_COMMENT_CHARS]
        text = "\n".join(part for part in (
            f"module {name}",
            (f"normalized module: {entry.get('normalized_name')}"
             if entry.get("normalized_name") and entry.get("normalized_name") != name else ""),
            f"instantiated by: {parents[name]}" if name in parents else "",
            f"ports: {', '.join(_unique(port_names))}" if port_names else "",
            f"instantiates: {', '.join(kids)}" if kids else "",
            f"structure: {structural_summary}" if structural_summary else "",
            f"comments: {said}" if said else "",
        ) if part)
        signals = _signal_metadata(entry)
        lookup_terms = [name, entry.get("original_name"), normalize_identifier(name)]
        records.append(_record(
            chunk_id=f"rtl:{name}",
            record_id=entry.get("module_id", f"rtl::mod::{name}"),
            context_header=f"RTL > {cfg.RTL_DIR.name}/{name}",
            text=text,
            source_type="module",
            module=name,
            entry=entry,
            lookup_terms=lookup_terms,
            line=entry.get("start_line", entry.get("line", 1)),
            start_line=entry.get("start_line", entry.get("line", 1)),
            end_line=entry.get("end_line", entry.get("line", 1)),
            original_name=entry.get("original_name", name),
            normalized_name=entry.get("normalized_name", normalize_identifier(name)),
            normalized_tokens=entry.get("normalized_tokens", []),
            signal_metadata=signals,
            module_content_hash=entry.get("content_hash"),
        ))
    return records


def _structural_records(rtl):
    """Project only useful module structural blocks into vector records."""
    records = []
    used_ids = set()
    fields = ("always_blocks", "functions_tasks", "assignment_groups")
    for module, entry in sorted(rtl.get("modules", {}).items()):
        blocks = [block for field in fields for block in entry.get(field, [])]
        blocks.sort(key=lambda block: (
            block.get("start_line", 0), block.get("end_line", 0),
            block.get("construct_type", block.get("kind", ""))))
        for block in blocks:
            first = block.get("start_line", entry.get("start_line", 1))
            last = block.get("end_line", first)
            kind = str(block.get("construct_type") or block.get("kind") or "STRUCTURE")
            surface = str(block.get("source_code") or block.get("source_text") or "").strip()
            surface = surface[:STRUCTURAL_SURFACE_CHARS]
            record_id = block.get("block_id") or f"rtl::blk::{module}::{kind}:{first}-{last}"
            legacy_kind = block.get("kind") or kind.lower()
            chunk_id = f"rtl:construct:{module}:{legacy_kind}:{first}-{last}"
            if chunk_id in used_ids:
                chunk_id = f"{chunk_id}:{content_hash(record_id)}"
            used_ids.add(chunk_id)
            reads = block.get("signals_read", [])
            writes = block.get("signals_written", [])
            name = block.get("original_name") or kind.lower()
            text = "\n".join(part for part in (
                f"{kind.lower()} in module {module}",
                f"reads: {', '.join(reads)}" if reads else "",
                f"writes: {', '.join(writes)}" if writes else "",
                f"source: {Path(block.get('file', entry['file'])).name}:{first}-{last}",
                surface,
            ) if part)
            records.append(_record(
                chunk_id=chunk_id,
                record_id=record_id,
                context_header=(f"RTL > {cfg.RTL_DIR.name}/{module} > "
                                f"{kind.lower()} lines {first}-{last}"),
                text=text,
                source_type="construct",
                module=module,
                entry={"file": block.get("file", entry["file"]), "start_line": first},
                # Named functions/tasks are exact-addressable; source operands are not.
                lookup_terms=[name] if "@" not in name else [],
                line=first,
                start_line=first,
                end_line=last,
                construct_type=kind,
                construct_name=name,
                source_code=surface,
                signals_read=reads,
                signals_written=writes,
                sensitivity=block.get("sensitivity", []),
                clock=block.get("clock"),
                reset=block.get("reset"),
                compiler_content_hash=block.get("content_hash"),
            ))
    return records


def retrieval_records(rtl, child_modules=None):
    """Return module records first, followed only by useful structural records."""
    child_modules = children(rtl.get("tree", [])) if child_modules is None else child_modules
    return [*chunks(rtl, child_modules), *_structural_records(rtl)]


def load_records(path=cfg.RTL_CHUNKS_PATH):
    """Load persisted retrieval records, or return an empty list when absent."""
    try:
        with open(path, encoding="utf-8") as source:
            return [json.loads(line) for line in source if line.strip()]
    except OSError:
        return []


def index_manifest_status(records, index_dir, manifest_path):
    """Compare current module/structure embedding surfaces with the saved manifest."""
    records = list(records)
    seen, duplicate_ids = set(), set()
    for record in records:
        if record["chunk_id"] in seen:
            duplicate_ids.add(record["chunk_id"])
        seen.add(record["chunk_id"])
    current = build_manifest(records)
    status = {
        "fresh": False,
        "reason": "",
        "index_dir": str(index_dir),
        "manifest_path": str(manifest_path),
        "current_count": len(current),
        "manifest_count": 0,
        "added_ids": [],
        "removed_ids": [],
        "changed_ids": [],
    }
    if not records:
        status["reason"] = "source records empty"
        return status
    if duplicate_ids:
        status.update(reason="duplicate chunk ids", duplicate_ids=sorted(duplicate_ids))
        return status
    if not Path(index_dir).is_dir():
        status["reason"] = "index missing"
        return status
    if not Path(manifest_path).is_file():
        status["reason"] = "manifest missing"
        return status
    try:
        saved = read_manifest(Path(manifest_path))
    except (OSError, ValueError, TypeError) as exc:
        status.update(reason="manifest unreadable", error=f"{type(exc).__name__}: {exc}")
        return status
    if (not isinstance(saved, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in saved.items())):
        status["reason"] = "manifest invalid"
        return status
    current_ids, saved_ids = set(current), set(saved)
    status.update(
        manifest_count=len(saved),
        added_ids=sorted(current_ids - saved_ids),
        removed_ids=sorted(saved_ids - current_ids),
        changed_ids=sorted(chunk_id for chunk_id in current_ids & saved_ids
                           if current[chunk_id] != saved[chunk_id]),
    )
    status["fresh"] = current == saved
    status["reason"] = "fresh" if status["fresh"] else "manifest mismatch"
    return status


def _query_terms(query):
    """Extract sufficiently distinctive identifier-like query terms."""
    return [term for term in _IDENTIFIER.findall(query)
            if (term.lower().startswith("0x") or len(term) >= 3)
            and term.casefold() not in _QUERY_STOP_WORDS]


def _term_strength(query_terms, candidates):
    """Score exact, case-folded, then normalized candidate-name matches."""
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
    """Score a multi-token module name spelled out verbatim inside the query."""
    tokens = str(normalized_name or "").split()
    if len(tokens) < 2:
        return None
    span = len(tokens)
    return 3 if any(query_tokens[start:start + span] == tokens
                    for start in range(len(query_tokens) - span + 1)) else None


def exact_lookup(query, records, k=None):
    """Resolve module/function names and module-owned signals before dense search."""
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
        found.append((strength, type_order, record.get("record_id", ""), position,
                      record, matched_signal))
    found.sort(key=lambda item: item[:4])
    documents = []
    for _, _, _, _, record, matched_signal in found:
        document = record_to_document(record)
        if matched_signal:
            document.metadata["exact_match_type"] = "signal"
            document.metadata["matched_signal"] = matched_signal
            document.metadata["symbol_name"] = matched_signal.get("original_name")
            document.metadata["roles"] = (
                (["driver"] if matched_signal.get("writer_sites") else [])
                + (["reader"] if matched_signal.get("reader_sites") else [])
                or ["reference"])
        documents.append(document)
    return documents[:k] if k is not None else documents


class RTLRetriever:
    """Keep compiler-metadata exact lookup ahead of hybrid semantic retrieval."""

    def __init__(self, records, store=None, hybrid=None):
        """Create a lazy RTL retriever.

        Args:
            records: Module-first RTL retrieval records.
            store: Optional dense-store test adapter.
            hybrid: Optional generic hybrid retriever adapter.

        Returns:
            None.
        """
        self.records = list(records)
        self.store = store
        self.hybrid = hybrid

    def _hybrid(self):
        """Load BM25, FAISS, and cross-encoder adapters only when exact lookup is short."""
        if self.hybrid is None:
            self.hybrid = HybridRetriever.load(
                records=self.records, index_dir=cfg.RTL_INDEX_DIR)
        return self.hybrid

    def search(self, query, top_k=None, k=None):
        """Return exact module/signal hits, then hybrid-reranked module/block rows.

        Args:
            query: RTL retrieval question.
            top_k: Maximum result count used by the generic retriever protocol.
            k: Equivalent maximum used by the existing RTL API.

        Returns:
            LangChain documents in final rank order.
        """
        wanted = top_k or k or cfg.AGENT_SEARCH_K
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
                selected = signal_exact[:max(wanted - 1, 1)]
                output = [*selected, *structures][:wanted]
                if output:
                    return output
            return exact[:wanted]
        if structures:
            exact_ids = {document.metadata.get("chunk_id") for document in exact}
            output = [*exact, *(document for document in structures
                                if document.metadata.get("chunk_id") not in exact_ids)]
            if len(output) >= wanted:
                return output[:wanted]
        if self.store is not None:
            ranked = self.store.search(query, k=wanted + len(exact))
        else:
            ranked = self._hybrid().search(query, top_k=wanted + len(exact))
        output = list(exact)
        seen = {document.metadata.get("chunk_id") for document in exact}
        for hit in ranked:
            document = hit[0] if isinstance(hit, tuple) else hit
            chunk_id = document.metadata.get("chunk_id")
            if chunk_id in seen:
                continue
            output.append(document)
            seen.add(chunk_id)
            if len(output) >= wanted:
                break
        return output


def search(query, k=None, records=None, store=None):
    """Return exact RTL metadata followed by a hybrid-reranked bounded fill."""
    available = load_records() if records is None else records
    return RTLRetriever(available, store=store).search(query, k=k)


def main():
    """Write retrieval records, rebuild the index if stale, and smoke-test it."""
    if not cfg.RTL_PATH.exists():
        raise SystemExit(f"no {cfg.RTL_PATH.name}; run src/rtl_ingest.py first")
    with open(cfg.RTL_PATH, encoding="utf-8") as source:
        rtl = json.load(source)
    records = retrieval_records(rtl)
    manifest = build_manifest(records)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(cfg.RTL_CHUNKS_PATH, "w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record) + "\n")
    modules = sum(record["source_type"] == "module" for record in records)
    structures = len(records) - modules
    print(f"{len(records)} RTL records ({modules} modules, {structures} structures) "
          f"-> {cfg.RTL_CHUNKS_PATH.relative_to(cfg.ROOT)}")
    status = index_manifest_status(records, cfg.RTL_INDEX_DIR, cfg.RTL_MANIFEST_PATH)
    if status["fresh"]:
        print(f"{len(records)} RTL records already embedded and unchanged, nothing to do")
        return
    store = VectorStore.build(records)
    store.save(cfg.RTL_INDEX_DIR)
    write_manifest(manifest, cfg.RTL_MANIFEST_PATH)
    print(f"indexed -> {cfg.RTL_INDEX_DIR.relative_to(cfg.ROOT)}")
    query = " ".join(sys.argv[1:]) or "floating point multiply and divide"
    print(f"\n{query!r} ->")
    for document, score in store.search(query, k=cfg.AGENT_SEARCH_K):
        print(f"  {score:.3f}  {document.metadata['module']}  "
              f"({document.metadata['file']}:{document.metadata['line']})")


if __name__ == "__main__":
    main()
