"""Project Verilator's RTL AST into a deterministic, module-centric artifact.

SystemVerilog is not parsed a second time.  Declarations, source extents, hierarchy,
bindings, sensitivity, and reader/writer sites all come from Verilator XML.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import configure as cfg
from naming import identifier_tokens, normalize_identifier, normalize_module_name


SCHEMA_VERSION = 4
_LOC = re.compile(r"^([a-zA-Z]+),(\d+),(\d+),(\d+),(\d+)$")
_CLONE_SUFFIX = re.compile(r"__[A-Za-z0-9_]+$")
_WARNING = re.compile(r"^%Warning-([A-Z]+):\s+(\S+?):(\d+):\d+:\s+(.*)$")
_ASSIGN_KINDS = ("assign", "assigndly", "assignw", "assignalias", "contassign")
_CONDITION_KINDS = ("if", "case")
_BLOCK_KINDS = ("always", "func", "task", "contassign", "initial")


def parse_loc(loc):
    """Return ``(file id, first line, last line)`` for a Verilator location.

    Args:
        loc: Raw Verilator ``loc`` attribute.

    Returns:
        ``(file id, first line, last line)``, or None when unparseable.
    """
    match = _LOC.match(loc or "")
    if not match:
        return None
    return match.group(1), int(match.group(2)), int(match.group(4))


def cited_path(path):
    """Return a source path as cited in ``rtl.json``: POSIX, relative to ``RTL_DIR``.

    Args:
        path: Absolute path under ``cfg.RTL_DIR``, or a path already relative to it.

    Returns:
        The POSIX path relative to the RTL directory.
    """
    path = Path(path)
    return (path.relative_to(cfg.RTL_DIR) if path.is_absolute() else path).as_posix()


def file_table(root, rtl_dir):
    """Map Verilator file ids to project SystemVerilog paths.

    Args:
        root: Parsed Verilator XML root element.
        rtl_dir: Directory holding the project's SystemVerilog sources.

    Returns:
        Verilator file id to the matching existing ``.sv`` path.
    """
    table = {}
    for element in root.iter("file"):
        name = element.get("filename", "")
        if not name.endswith(".sv"):
            continue
        path = Path(rtl_dir) / Path(name).name
        if path.exists():
            table[element.get("id")] = path
    return table


def source_name(name):
    """Collapse a Verilator parameter clone to its source module name.

    Args:
        name: Possibly parameter-specialized module name.

    Returns:
        The source module name with any clone suffix removed.
    """
    return _CLONE_SUFFIX.sub("", name or "")


def read_lines(path, first, last):
    """Read an inclusive, one-based source line range.

    Args:
        path: Source file to read.
        first: First one-based line to include.
        last: Last one-based line to include.

    Returns:
        The requested lines joined as one string.
    """
    with open(path, encoding="utf-8", errors="replace") as source:
        return "".join(source.readlines()[first - 1:last])


def block_extent(node):
    """Return a compiler-bounded block extent without following declaration refs.

    Args:
        node: Verilator AST node opening the block.

    Returns:
        ``(file id, first line, last line)``, or None without a usable location.
    """
    own = parse_loc(node.get("loc"))
    if not own:
        return None
    if node.tag in _ASSIGN_KINDS:
        return own
    letter, first, last = own
    for child in node.iter():
        located = parse_loc(child.get("loc"))
        if located and located[0] == letter:
            last = max(last, located[2])
    return letter, first, last


def _unique(values):
    """Deduplicate non-empty values while preserving compiler order.

    Args:
        values: Values to deduplicate.

    Returns:
        Unique non-empty values in first-seen order.
    """
    out = []
    for value in values:
        if value not in (None, "") and value not in out:
            out.append(value)
    return out


def _normal_direction(direction):
    """Normalize Verilator's abbreviated port directions.

    Args:
        direction: Raw Verilator direction attribute.

    Returns:
        ``input``, ``output``, or the value unchanged.
    """
    return {"in": "input", "out": "output"}.get(direction, direction)


def _parent_map(root):
    """Build child-to-parent links for ElementTree nodes.

    Args:
        root: Parsed Verilator XML root element.

    Returns:
        Child element to its parent element.
    """
    return {child: parent for parent in root.iter() for child in parent}


def _owned_by(node, owner, parents):
    """Return whether a node's nearest module ancestor is ``owner``.

    Args:
        node: AST node to test.
        owner: Module element expected to own it.
        parents: Child-to-parent map from ``_parent_map``.

    Returns:
        True when the node belongs to ``owner`` rather than a nested module.
    """
    current = parents.get(node)
    while current is not None:
        if current.tag == "module":
            return current is owner
        current = parents.get(current)
    return False


def _scope_names(node, parents, stop):
    """Return named compiler scopes enclosing a node inside its module.

    Args:
        node: AST node to walk up from.
        parents: Child-to-parent map from ``_parent_map``.
        stop: Ancestor element at which to stop, normally the module.

    Returns:
        Named enclosing scopes, outermost first.
    """
    names = []
    current = parents.get(node)
    while current is not None and current is not stop:
        if current.tag in {"begin", "genfor", "genif", "generate", "func", "task"}:
            name = current.get("name") or current.get("origName")
            if name:
                names.append(name)
        current = parents.get(current)
    return list(reversed(names))


def _symbol_kind(var):
    """Classify a declaration as a port, parameter, or internal signal.

    Args:
        var: Verilator ``var`` declaration element.

    Returns:
        ``PORT``, ``PARAMETER``, or ``SIGNAL``.
    """
    if (var.get("param") in {"true", "1"}
            or var.get("localparam") in {"true", "1"}
            or var.get("vartype") in {"param", "localparam"}):
        return "PARAMETER"
    return "PORT" if var.get("dir") else "SIGNAL"


def _symbol_base_id(kind, module, name):
    """Build the canonical identifier for a module-owned declaration.

    Args:
        kind: Declaration kind from ``_symbol_kind``.
        module: Owning source module name.
        name: Declared identifier.

    Returns:
        The unscoped canonical ``rtl::`` identifier for that declaration.
    """
    prefix = {"PORT": "port", "PARAMETER": "param", "SIGNAL": "sig"}[kind]
    return f"rtl::{prefix}::{module}::{name}"


def _integer(value):
    """Decode an ordinary integer used in Verilator type ranges.

    Args:
        value: Decimal or sized-based Verilog literal.

    Returns:
        The integer value, or None when the literal is not a plain constant.
    """
    if value is None:
        return None
    text = str(value).replace("_", "").strip()
    match = re.fullmatch(r"(?:\d+)?'[sS]?([bBoOdDhH])([0-9a-fA-F]+)", text)
    if match:
        base = {"b": 2, "o": 8, "d": 10, "h": 16}[match.group(1).lower()]
        return int(match.group(2), base)
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    return None


def _range_descriptor(node):
    """Return numeric width and source-like range text for a dtype node.

    Args:
        node: Verilator dtype element.

    Returns:
        ``(width, range expression)``; either is None when not deterministic.
    """
    left, right = node.get("left"), node.get("right")
    if left is None or right is None:
        range_node = node.find("range")
        constants = list(range_node.iter("const")) if range_node is not None else []
        if len(constants) >= 2:
            left, right = constants[0].get("name"), constants[1].get("name")
    if left is None or right is None:
        return None, None
    expression = f"[{left}:{right}]"
    left_value, right_value = _integer(left), _integer(right)
    width = (abs(left_value - right_value) + 1
             if left_value is not None and right_value is not None else None)
    return width, expression


def type_descriptors(root):
    """Resolve Verilator dtype ids to type and packed-width metadata.

    Args:
        root: Parsed Verilator XML root element.

    Returns:
        Dtype id to a descriptor with data type, width, and width expression.
    """
    table = {}
    type_table = root.find("typetable")
    if type_table is None:
        type_table = root.find(".//typetable")
    if type_table is None:
        return table
    scalar_widths = {
        "bit": 1, "logic": 1, "reg": 1, "byte": 8, "shortint": 16,
        "int": 32, "integer": 32, "longint": 64, "time": 64,
    }
    nodes = {node.get("id"): node for node in type_table if node.get("id")}

    def resolve(dtype_id, stack=()):
        """Recursively resolve one dtype while breaking malformed cycles.

        Args:
            dtype_id: Dtype identifier to resolve.
            stack: Dtype ids already being resolved on this path.

        Returns:
            The resolved descriptor, or an empty mapping when unresolvable.
        """
        if not dtype_id or dtype_id in table:
            return table.get(dtype_id, {})
        if dtype_id in stack or dtype_id not in nodes:
            return {}
        node = nodes[dtype_id]
        data_type = node.get("name") or node.tag.removesuffix("dtype")
        width, expression = _range_descriptor(node)
        child = resolve(node.get("sub_dtype_id"), (*stack, dtype_id))
        if width is None and node.tag == "basicdtype":
            width = scalar_widths.get(data_type)
        elif width is not None and node.tag in {"packarraydtype", "unpackarraydtype"}:
            child_width = child.get("width")
            width = width * child_width if child_width is not None else None
        elif width is None and child:
            width = child.get("width")
            expression = child.get("width_expression")
        descriptor = {"data_type": data_type or child.get("data_type")}
        if width is not None:
            descriptor["width"] = width
        if expression is not None:
            descriptor["width_expression"] = expression
        table[dtype_id] = descriptor
        return descriptor

    for dtype_id in nodes:
        resolve(dtype_id)
    return table


def _enum_item(node, files):
    """Project one enumeration member with its constant and declaration site.

    Args:
        node: Verilator ``enumitem`` element.
        files: Verilator file id to source path.

    Returns:
        One member record with its value and declaration site.
    """
    constant = next((child for child in node if child.tag == "const"), None)
    located = parse_loc(node.get("loc"))
    path = files.get(located[0]) if located else None
    name = node.get("name") or ""
    return {
        "original_name": name,
        "normalized_name": normalize_identifier(name),
        "value": _integer(constant.get("name")) if constant is not None else None,
        "value_source": constant.get("name") if constant is not None else None,
        "file": cited_path(path) if path else None,
        "line": located[1] if located else None,
    }


def enum_types(root, files):
    """Return package-scoped enumeration types with their members in source order.

    Args:
        root: Parsed Verilator XML root element.
        files: Verilator file id to source path.

    Returns:
        Enumeration records ordered by their stable identifier.
    """
    records = {}
    for node in root.iter("enumdtype"):
        package, _, name = (node.get("name") or "").rpartition("::")
        if not name:
            continue
        enum_id = "rtl::enum::" + (f"{package}::{name}" if package else name)
        if enum_id in records:
            continue
        items = [_enum_item(item, files) for item in node.iter("enumitem")]
        records[enum_id] = {
            "enum_id": enum_id,
            "original_name": name,
            "package": package or None,
            "normalized_name": normalize_identifier(name),
            "normalized_tokens": identifier_tokens(name),
            "items": items,
        }
    return [records[enum_id] for enum_id in sorted(records)]


def _declared_names(module):
    """Return every identifier one module declares in its own scope.

    Args:
        module: One assembled module record.

    Returns:
        The module's declared port, signal, and parameter names.
    """
    return {item.get("original_name") for key in ("ports", "signals", "parameters")
            for item in module.get(key, []) if item.get("original_name")}


def _usage_blocks(module_records):
    """Return every structural block that carries source, paired with its module.

    Args:
        module_records: Assembled module records keyed by module name.

    Returns:
        ``(module name, block)`` pairs in module-name order.
    """
    return [(name, block) for name, module in sorted(module_records.items())
            for key in ("always_blocks", "functions_tasks", "assignment_groups",
                        "other_blocks")
            for block in module.get(key, []) if block.get("source_code")]


def enum_usage(enums, module_records):
    """Attach the blocks naming each enumeration member outside a shadowing scope.

    A member mentioned by a module that also declares that identifier is ambiguous,
    so the site is left out rather than credited to the enumeration.

    Args:
        enums: Enumeration records from ``enum_types``.
        module_records: Assembled module records keyed by module name.

    Returns:
        Copies of the enumerations whose members carry their usage sites.
    """
    declared = {name: _declared_names(module) for name, module in module_records.items()}
    blocks = _usage_blocks(module_records)
    annotated = []
    for enum in enums:
        items = []
        for item in enum["items"]:
            name = item["original_name"]
            pattern = re.compile(r"\b" + re.escape(name) + r"\b") if name else None
            items.append({**item, "usage_sites": [
                {"module": module, "construct_type": block.get("construct_type"),
                 "file": block.get("file"), "line": block.get("start_line")}
                for module, block in blocks
                if pattern is not None and name not in declared[module]
                and pattern.search(block["source_code"])]})
        annotated.append({**enum, "items": items})
    return annotated


def _declaration_record(var, module, kind, name, located, scopes, files, order, dtypes):
    """Project one compiler-owned declaration before stable-id assignment.

    Args:
        var: Verilator ``var`` declaration element.
        module: Owning source module name.
        kind: Declaration kind from ``_symbol_kind``.
        name: Declared identifier.
        located: Parsed ``(file id, first line, last line)`` for the declaration.
        scopes: Named enclosing scopes inside the module.
        files: Verilator file id to source path.
        order: Compiler discovery position, used to restore source order.
        dtypes: Dtype descriptors from ``type_descriptors``.

    Returns:
        A declaration record still carrying its private ordering fields.
    """
    path = files[located[0]]
    source_text = read_lines(path, located[1], located[2])
    value_node = next(var.iter("const"), None)
    descriptor = dtypes.get(var.get("dtype_id"), {})
    record = {
        "_order": order,
        "_scopes": scopes,
        "_raw_loc": var.get("loc"),
        "construct_type": kind,
        "symbol_name": name,
        "original_name": name,
        "normalized_name": normalize_identifier(name),
        "normalized_tokens": identifier_tokens(name),
        "module_name": module,
        "file_path": cited_path(path),
        "start_line": located[1],
        "end_line": located[2],
        "direction": _normal_direction(var.get("dir")) or "internal",
        "data_type": var.get("vartype") or descriptor.get("data_type") or var.get("dtype_id"),
        "parameter_kind": ("localparam" if var.get("localparam") in {"true", "1"}
                           else "parameter" if kind == "PARAMETER" else None),
        "value": value_node.get("name") if value_node is not None else None,
        "hierarchical_path": [module, *scopes, name],
        "source_text": source_text,
    }
    if descriptor.get("width") is not None:
        record["width"] = descriptor["width"]
    if descriptor.get("width_expression") is not None:
        record["width_expression"] = descriptor["width_expression"]
    return record


def _assign_symbol_ids(candidates):
    """Assign deterministic scoped IDs and hashes to declaration candidates.

    Args:
        candidates: Declaration records keyed by their discovery key.

    Returns:
        Declaration records in compiler order, with IDs and hashes set.
    """
    grouped = {}
    for record in candidates.values():
        grouped.setdefault((record["module_name"], record["symbol_name"]), []).append(record)
    records = []
    for group in grouped.values():
        direct = [record for record in group if not record["_scopes"]]
        for record in group:
            base = _symbol_base_id(record["construct_type"], record["module_name"],
                                   record["symbol_name"])
            if len(group) == 1 or (len(direct) == 1 and record is direct[0]):
                symbol_id = base
            else:
                raw = _LOC.match(record["_raw_loc"] or "")
                position = (f"{raw.group(2)}:{raw.group(3)}-{raw.group(4)}:{raw.group(5)}"
                            if raw else f"{record['start_line']}:0")
                scope = "::".join([*record["_scopes"], position])
                symbol_id = f"{base}::{scope}"
            record["signal_id"] = symbol_id
            record.pop("_scopes")
            record.pop("_raw_loc")
            records.append(record)
    records.sort(key=lambda record: record.pop("_order"))
    return records


def symbol_records(root, files):
    """Return deduplicated declarations for assembling module metadata.

    Args:
        root: Parsed Verilator XML root element.
        files: Verilator file id to source path.

    Returns:
        Declaration records in compiler order with stable identifiers.
    """
    parents = _parent_map(root)
    dtypes = type_descriptors(root)
    candidates = {}
    order = 0
    for element in root.iter("module"):
        module_loc = parse_loc(element.get("loc"))
        if not module_loc or module_loc[0] not in files:
            continue
        module = source_name(element.get("origName") or element.get("name"))
        for var in element.iter("var"):
            if not _owned_by(var, element, parents):
                continue
            located = parse_loc(var.get("loc"))
            name = var.get("name")
            if not located or located[0] not in files or not name:
                continue
            kind = _symbol_kind(var)
            scopes = _scope_names(var, parents, element)
            key = (module, kind, name, var.get("loc"), tuple(scopes))
            # Parameter clones repeat a declaration; the source record is kept once.
            if key in candidates:
                continue
            candidates[key] = _declaration_record(
                var, module, kind, name, located, scopes, files, order, dtypes)
            order += 1
    return _assign_symbol_ids(candidates)


def _symbol_lookup(records):
    """Index declarations whose module/name pair resolves unambiguously.

    Args:
        records: Declaration records from ``symbol_records``.

    Returns:
        ``(module, name)`` to its single unambiguous declaration.
    """
    grouped = {}
    for record in records:
        grouped.setdefault((record["module_name"], record["symbol_name"]), []).append(record)
    out = {}
    for key, matches in grouped.items():
        # The direct declaration is the one that received the unscoped identifier.
        direct = [record for record in matches
                  if record["signal_id"] == _symbol_base_id(
                      record["construct_type"], record["module_name"], record["symbol_name"])]
        if len(matches) == 1:
            out[key] = matches[0]
        elif len(direct) == 1:
            out[key] = direct[0]
    return out


def ref_names(node):
    """Return compiler-ordered signal references inside a subtree.

    Args:
        node: AST node to scan.

    Returns:
        Referenced signal names in compiler order, duplicates kept.
    """
    return [ref.get("name") for ref in node.iter("varref") if ref.get("name")]


def _assignment_signal_roles(node):
    """Split an assignment's AST references into reads and writes.

    Args:
        node: Verilator assignment element.

    Returns:
        ``(read names, written names)``, each deduplicated in order.
    """
    if node.tag not in _ASSIGN_KINDS or len(node) < 2:
        return [], []
    lhs = ref_names(node[-1])
    written = lhs[:1]
    read = lhs[1:]
    for operand in node[:-1]:
        read.extend(ref_names(operand))
    return _unique(read), _unique(written)


def _construct_signal_roles(node):
    """Aggregate sensitivity, condition, and assignment roles for a block.

    Args:
        node: Verilator block element.

    Returns:
        ``(read names, written names)``, each deduplicated in order.
    """
    read, written = [], []
    for child in node.iter():
        if child.tag == "senitem":
            read.extend(ref_names(child))
        elif child.tag in _CONDITION_KINDS and len(child):
            read.extend(ref_names(child[0]))
        elif child.tag in _ASSIGN_KINDS:
            child_read, child_written = _assignment_signal_roles(child)
            read.extend(child_read)
            written.extend(child_written)
    return _unique(read), _unique(written)


def _sensitivity(node):
    """Extract ordered signal-and-edge sensitivity descriptors.

    Args:
        node: Verilator block element carrying sensitivity items.

    Returns:
        Unique ``{signal, edge}`` descriptors in compiler order.
    """
    edges = {"POS": "posedge", "NEG": "negedge", "BOTH": "edge",
             "ANY": "change", "COMBO": "change"}
    found = []
    for item in node.iter("senitem"):
        edge = edges.get(item.get("edgeType"), (item.get("edgeType") or "change").lower())
        for name in ref_names(item):
            descriptor = {"signal": name, "edge": edge}
            if descriptor not in found:
                found.append(descriptor)
    return found


def _block_type(kind, text):
    """Refine a compiler block tag to its useful structural category.

    Args:
        kind: Verilator block tag.
        text: Block source text, used to read the always flavour.

    Returns:
        A structural category such as ``ALWAYS_FF`` or ``FUNCTION``.
    """
    if kind == "always":
        first_line = text.splitlines()[0] if text else ""
        match = re.search(r"\balways_(ff|comb|latch)\b", first_line)
        return f"ALWAYS_{match.group(1).upper()}" if match else "PROCESS"
    return {"func": "FUNCTION", "task": "TASK", "contassign": "ASSIGNMENT_GROUP",
            "initial": "INITIAL"}.get(kind, kind.upper())


def statement_site(node, module, files):
    """Return a citable source site for a compiler statement.

    Args:
        node: Verilator statement element.
        module: Owning source module name.
        files: Verilator file id to source path.

    Returns:
        A ``{module, file, line}`` site, or None when it is not citable.
    """
    located = parse_loc(node.get("loc"))
    if not located or located[0] not in files:
        return None
    return {"module": module, "file": cited_path(files[located[0]]), "line": located[1]}


def collapse_sites(found):
    """Deduplicate and deterministically order reader or writer sites.

    Args:
        found: Signal name to the sites collected for it.

    Returns:
        Signal name to its unique sites, ordered by file, line, and module.
    """
    out = {}
    for name, sites in sorted(found.items()):
        unique = {(site["file"], site["line"], site["module"]): site for site in sites}
        out[name] = [unique[key] for key in sorted(unique)]
    return out


def driver_reader_sites(root, files):
    """Return deterministic compiler-derived writer and reader sites.

    Args:
        root: Parsed Verilator XML root element.
        files: Verilator file id to source path.

    Returns:
        ``(writer sites, reader sites)``, each keyed by signal name.
    """
    driven, read = {}, {}
    for element in root.iter("module"):
        located = parse_loc(element.get("loc"))
        if not located or located[0] not in files:
            continue
        module = source_name(element.get("origName") or element.get("name"))
        for node in element.iter():
            assigning = node.tag in _ASSIGN_KINDS and len(node) >= 2
            testing = node.tag in _CONDITION_KINDS and len(node) >= 1
            if not assigning and not testing:
                continue
            site = statement_site(node, module, files)
            if not site:
                continue
            if testing:
                for name in ref_names(node[0]):
                    read.setdefault(name, []).append(site)
                continue
            statement_read, statement_written = _assignment_signal_roles(node)
            for name in statement_written:
                driven.setdefault(name, []).append(site)
            for name in statement_read:
                read.setdefault(name, []).append(site)
    return collapse_sites(driven), collapse_sites(read)


def _instance_record(node, element, module, files, symbols, parents):
    """Project an instance and its compiler-resolved formal/actual bindings.

    Args:
        node: Verilator ``instance`` element.
        element: Elaborated module element containing the instance.
        module: Owning source module name.
        files: Verilator file id to source path.
        symbols: Unambiguous declaration lookup from ``_symbol_lookup``.
        parents: Child-to-parent map from ``_parent_map``.

    Returns:
        An instance record with its port bindings, or None when not citable.
    """
    located = parse_loc(node.get("loc"))
    if not located or located[0] not in files:
        return None
    path = files[located[0]]
    instance_name = node.get("origName") or node.get("name") or "instance"
    elaborated_target = node.get("defName") or node.get("submodname") or ""
    target = source_name(elaborated_target)
    raw_match = _LOC.match(node.get("loc", ""))
    column = raw_match.group(3) if raw_match else "0"
    instance_id = f"rtl::instdef::{module}::{instance_name}::{located[1]}:{column}"
    scopes = _scope_names(node, parents, element)
    bindings = []
    for port in node.findall("port"):
        port_loc = parse_loc(port.get("loc"))
        formal = port.get("name")
        if not formal or not port_loc or port_loc[0] not in files:
            continue
        actual_refs = _unique(ref.get("name") for ref in port.iter("varref"))
        actual_symbol_ids = [symbols[(module, name)]["signal_id"] for name in actual_refs
                             if (module, name) in symbols]
        formal_record = symbols.get((target, formal))
        formal_id = (formal_record["signal_id"] if formal_record
                     else _symbol_base_id("PORT", target, formal))
        citation_path = files[port_loc[0]]
        citation = read_lines(citation_path, port_loc[1], port_loc[2])
        bindings.append({
            "parent_module": module,
            "target_module": target,
            "formal_name": formal,
            "formal_signal_id": formal_id,
            "actual_signals": actual_refs,
            "actual_signal_ids": actual_symbol_ids,
            "direction": _normal_direction(port.get("direction")),
            "expression_kind": port[0].tag if len(port) else "open",
            "file": cited_path(citation_path),
            "file_path": cited_path(citation_path),
            "line": port_loc[1],
            "start_line": port_loc[1],
            "end_line": port_loc[2],
            "source_text": citation,
        })
    source_text = read_lines(path, located[1], located[2])
    return {
        "instance_id": instance_id,
        "construct_id": instance_id,
        "construct_type": "INSTANCE",
        "original_name": instance_name,
        "symbol_name": instance_name,
        "normalized_name": normalize_identifier(instance_name),
        "normalized_tokens": identifier_tokens(instance_name),
        "module_name": module,
        "target_module": target,
        "file": cited_path(path),
        "file_path": cited_path(path),
        "line": located[1],
        "start_line": located[1],
        "end_line": located[2],
        "hierarchical_path": [module, *scopes, instance_name],
        "source_text": source_text,
        "bindings": bindings,
    }


def _module_extents(root, files):
    """Bound modules with compiler starts and the next module/file boundary.

    Args:
        root: Parsed Verilator XML root element.
        files: Verilator file id to source path.

    Returns:
        Module name to ``(file id, first line, last line)``.
    """
    starts = {}
    for element in root.iter("module"):
        located = parse_loc(element.get("loc"))
        if located and located[0] in files:
            module = source_name(element.get("origName") or element.get("name"))
            starts.setdefault((located[0], located[1]), module)
    grouped = {}
    for (letter, line), module in starts.items():
        grouped.setdefault(letter, []).append((line, module))
    extents = {}
    for letter, members in grouped.items():
        members.sort()
        with open(files[letter], encoding="utf-8", errors="replace") as source:
            file_end = len(source.readlines())
        for index, (line, module) in enumerate(members):
            end = members[index + 1][0] - 1 if index + 1 < len(members) else file_end
            extents[module] = (letter, line, max(line, end))
    return extents


def module_preamble(extents, name, path):
    """Return the source lines above a module, back to the previous module in its file.

    Args:
        extents: Module name to ``(file id, first line, last line)``.
        name: Module whose preamble is wanted.
        path: Source file holding the module.

    Returns:
        The lines before the module's first line and after the previous module's last
        line in the same file, as written; empty when the two touch.
    """
    letter, first, _ = extents[name]
    start = 1 + max((last for other, (other_letter, _, last) in extents.items()
                     if other_letter == letter and last < first), default=0)
    return read_lines(path, start, first - 1) if first > start else ""


def _block_record(node, module, element, files, parents):
    """Project one useful compiler-bounded structural block.

    Args:
        node: Verilator block element.
        module: Owning source module name.
        element: Elaborated module element containing the block.
        files: Verilator file id to source path.
        parents: Child-to-parent map from ``_parent_map``.

    Returns:
        A block record with roles and sensitivity, or None when not citable.
    """
    extent = block_extent(node)
    if not extent or extent[0] not in files:
        return None
    letter, first, last = extent
    source_text = read_lines(files[letter], first, last)
    read, written = _construct_signal_roles(node)
    sensitivity = _sensitivity(node)
    raw = _LOC.match(node.get("loc", ""))
    column = raw.group(3) if raw else "0"
    kind = node.tag
    return {
        "block_id": f"rtl::blk::{module}::{kind}::{first}:{column}-{last}",
        "kind": kind,
        "construct_type": _block_type(kind, source_text),
        "original_name": node.get("origName") or node.get("name") or f"{kind}@{first}",
        "file": cited_path(files[letter]),
        "start_line": first,
        "end_line": last,
        "hierarchical_path": [module, *_scope_names(node, parents, element)],
        "source_code": source_text,
        "signals_read": read,
        "signals_written": written,
        "sensitivity": sensitivity,
    }


def _assignment_groups(module, records):
    """Group adjacent continuous assignments into useful retrieval units.

    Args:
        module: Owning source module name.
        records: Continuous-assignment block records for that module.

    Returns:
        Assignment-group records in source order, each with a stable ID.
    """
    groups = []
    for record in sorted(records, key=lambda item: (item["start_line"], item["end_line"])):
        if (groups and record["file"] == groups[-1]["file"]
                and record.get("hierarchical_path") == groups[-1].get("hierarchical_path")
                and record["start_line"] <= groups[-1]["end_line"] + 1):
            group = groups[-1]
            group["end_line"] = max(group["end_line"], record["end_line"])
            group["assignments"].append(record)
            group["signals_read"] = _unique([*group["signals_read"], *record["signals_read"]])
            group["signals_written"] = _unique([
                *group["signals_written"], *record["signals_written"]])
            group["source_code"] += record["source_code"]
        else:
            groups.append({
                "block_id": "",
                "kind": "assignment_group",
                "construct_type": "ASSIGNMENT_GROUP",
                "original_name": f"assignments@{record['start_line']}",
                "file": record["file"],
                "hierarchical_path": record.get("hierarchical_path", [module]),
                "start_line": record["start_line"],
                "end_line": record["end_line"],
                "source_code": record["source_code"],
                "signals_read": list(record["signals_read"]),
                "signals_written": list(record["signals_written"]),
                "assignments": [record],
            })
    for group in groups:
        group["block_id"] = (f"rtl::assign_group::{module}::"
                             f"{group['start_line']}-{group['end_line']}")
    return groups


def modules(root, files, declarations=None):
    """Build deterministic source-module records from the Verilator AST.

    Args:
        root: Parsed Verilator XML root element.
        files: Verilator file id to source path.
        declarations: Optional precomputed declarations from ``symbol_records``.

    Returns:
        Module records keyed by module name, in module-name order.
    """
    declarations = symbol_records(root, files) if declarations is None else declarations
    symbol_lookup = _symbol_lookup(declarations)
    writers, readers = driver_reader_sites(root, files)
    parents = _parent_map(root)
    extents = _module_extents(root, files)
    out = {}
    for element in root.iter("module"):
        located = parse_loc(element.get("loc"))
        if not located or located[0] not in files:
            continue
        name = source_name(element.get("origName") or element.get("name"))
        letter, first, last = extents[name]
        path = files[letter]
        entry = out.setdefault(name, {
            "module_id": f"rtl::mod::{name}",
            "original_name": name,
            "normalized_name": normalize_module_name(name),
            "normalized_tokens": identifier_tokens(name),
            "file": cited_path(path),
            "start_line": first,
            "end_line": last,
            "ports": [],
            "signals": [],
            "parameters": [],
            "instances": [],
            "functions_tasks": [],
            "always_blocks": [],
            "assignment_groups": [],
            "other_blocks": [],
            "source_code": read_lines(path, first, last),
            "preamble": module_preamble(extents, name, path),
            "_blocks": {},
            "_instances": {},
        })
        for node in element.iter():
            if node.tag not in _BLOCK_KINDS or not _owned_by(node, element, parents):
                continue
            record = _block_record(node, name, element, files, parents)
            if record:
                entry["_blocks"].setdefault((node.tag, node.get("loc"), node.get("name")),
                                            record)
        for node in element.iter("instance"):
            if not _owned_by(node, element, parents):
                continue
            record = _instance_record(node, element, name, files, symbol_lookup, parents)
            if record:
                # Parameter clones repeat an instance; the source record is kept once.
                key = (node.get("loc"), record["original_name"], record["target_module"])
                entry["_instances"].setdefault(key, record)

    for name, entry in out.items():
        for declaration in declarations:
            if declaration["module_name"] != name:
                continue
            record = dict(declaration)
            record["reader_sites"] = [site for site in readers.get(record["symbol_name"], [])
                                      if site["module"] == name]
            record["writer_sites"] = [site for site in writers.get(record["symbol_name"], [])
                                      if site["module"] == name]
            target = {"PORT": "ports", "SIGNAL": "signals", "PARAMETER": "parameters"}[
                record["construct_type"]]
            entry[target].append(record)
        blocks = sorted(entry.pop("_blocks").values(),
                        key=lambda item: (item["start_line"], item["end_line"], item["kind"]))
        entry["always_blocks"] = [block for block in blocks if block["kind"] == "always"]
        entry["functions_tasks"] = [block for block in blocks
                                    if block["kind"] in {"func", "task"}]
        entry["assignment_groups"] = _assignment_groups(
            name, [block for block in blocks if block["kind"] == "contassign"])
        entry["other_blocks"] = [block for block in blocks if block["kind"] == "initial"]
        entry["instances"] = sorted(entry.pop("_instances").values(),
                                    key=lambda item: (item["start_line"], item["original_name"]))
    return dict(sorted(out.items()))


def tree(root):
    """Flatten Verilator's authoritative instance hierarchy.

    Args:
        root: Parsed Verilator XML root element.

    Returns:
        Instance nodes in pre-order, each carrying its depth and path.
    """
    nodes = []

    def walk(element, depth, fallback=()):
        """Append cell descendants in Verilator's authoritative pre-order.

        Args:
            element: Element whose children are visited.
            depth: Instance depth of ``element``.
            fallback: Instance names used when the compiler states no hierarchy.

        Returns:
            None; visited cells are appended to the enclosing ``nodes`` list.
        """
        for child in element:
            if child.tag == "cell":
                fallback_path = (*fallback, child.get("name"))
                hier = child.get("hier") or ".".join(part for part in fallback_path if part)
                nodes.append({
                    "depth": depth,
                    "instance": child.get("name"),
                    "module": source_name(child.get("submodname")),
                    "hierarchical_path": hier.split(".") if hier else [],
                })
                walk(child, depth + 1, fallback_path)
            else:
                walk(child, depth, fallback)

    cells = root.find("cells")
    if cells is not None:
        walk(cells, 0)
    return nodes


def parse_warnings(text):
    """Turn Verilator lint diagnostics into structured records.

    Args:
        text: Raw Verilator lint output.

    Returns:
        One record per recognized warning line.
    """
    out = []
    for line in text.splitlines():
        match = _WARNING.match(line)
        if match:
            out.append({"code": match.group(1), "file": cited_path(match.group(2)),
                        "line": int(match.group(3)), "message": match.group(4).strip()})
    return out


def run_verilator(extra, mdir=None):
    """Run Verilator and fail before writing an artifact when it reports an error.

    Raises SystemExit when Verilator reports an error.

    Args:
        extra: Mode arguments placed before the configured arguments.
        mdir: Optional Verilator output directory.

    Returns:
        Combined standard output and standard error.
    """
    import subprocess

    args = ["verilator", *extra, *cfg.VERILATOR_ARGS]
    if mdir:
        args += ["--Mdir", str(mdir)]
    result = subprocess.run(args, capture_output=True, text=True)
    output = result.stdout + result.stderr
    if "%Error" in output:
        raise SystemExit(f"verilator reported errors; rtl.json not written:\n{output}")
    return output


def build(xml_path, lint_text):
    """Assemble the versioned module-centric RTL artifact.

    Args:
        xml_path: Verilator XML dump to read.
        lint_text: Raw lint output collected from the lint-only pass.

    Returns:
        The complete ``rtl.json`` payload.
    """
    root = ET.parse(xml_path).getroot()
    files = file_table(root, cfg.RTL_DIR)
    declarations = symbol_records(root, files)
    module_records = modules(root, files, declarations)
    top = next((source_name(element.get("origName") or element.get("name"))
                for element in root.iter("module") if element.get("topModule") in {"1", "true"}),
               source_name(cfg.RTL_TOP_MODULE))
    return {
        "schema_version": SCHEMA_VERSION,
        "top_module": top,
        "tree": tree(root),
        "modules": module_records,
        "enums": enum_usage(enum_types(root, files), module_records),
        "warnings": parse_warnings(lint_text),
    }


def main():
    """Run Verilator and write ``output/rtl.json``.

    Returns:
        None.
    """
    import json
    import tempfile

    if not cfg.RTL_DIR.exists():
        raise SystemExit(f"no RTL at {cfg.RTL_DIR}")
    lint_text = run_verilator(["--lint-only"])
    with tempfile.TemporaryDirectory() as mdir:
        run_verilator(["--xml-only"], mdir)
        data = build(Path(mdir) / f"V{cfg.RTL_TOP_MODULE}.xml", lint_text)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(cfg.RTL_PATH, "w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=1)
    structural = sum(
        len(module["always_blocks"]) + len(module["functions_tasks"])
        + len(module["assignment_groups"]) + len(module["other_blocks"])
        for module in data["modules"].values())
    print(f"====  wrote {cfg.RTL_PATH.relative_to(cfg.ROOT)}: "
          f"{len(data['modules'])} modules, {len(data['tree'])} instances, "
          f"{structural} structural blocks, {len(data['warnings'])} warnings")


if __name__ == "__main__":
    main()
