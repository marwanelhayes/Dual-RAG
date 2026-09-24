"""Convert PDF specifications into hierarchy-linked, provenance-rich chunks.

Run ``python src/ingest.py`` to read ``Ingest/*.pdf`` and write
``output/spec_hierarchy.json`` plus ``output/chunks.jsonl``.
"""

import collections
import hashlib
import json
import re
from operator import itemgetter
from pathlib import Path
from string import ascii_uppercase

import fitz  # PyMuPDF

import configure as cfg
import roles

SCHEMA_VERSION = 2

# TODO: Extract captioned figures by block gaps; captionless waveform.pdf needs vision.


def content_hash(*parts):
    """Fold several strings into one short, stable hex fingerprint.

    Args:
        *parts: the strings to fingerprint together, in order.

    Returns:
        a 16-character hex digest that is identical across runs and machines.
    """
    # NUL delimiters distinguish partitions such as ("ab", "c") and ("a", "bc").
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    # A readable 64-bit prefix is ample for this corpus's few thousand chunks.
    return h.hexdigest()[:16]


def load_chunks(path=cfg.CHUNKS_PATH):
    """Read the chunk records the ingest stage wrote.

    Args:
        path: the JSONL file to read.

    Returns:
        list of chunk dicts, one per line.
    """
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def slugify(name):
    """Turn a document filename into a short stable id.

    Args:
        name: file basename, e.g. "AXI spec.pdf".

    Returns:
        lowercase slug with non-alphanumerics collapsed to underscores.
    """
    return re.sub(r"[^a-z0-9]+", "_", Path(name).stem.lower()).strip("_")


# Vendor-specific revision dialects are tried from most to least specific.
_REVISION_PATTERNS = [
    ("Version", re.compile(r"\bVersion\s+(\d{6,8})\b")),
    ("Issue", re.compile(r"\bIssue\s+([A-Z]\d?|\d{2,4})\b")),
    ("Revision", re.compile(r"\bRevision[:\s]+([A-Za-z0-9.\-]+)")),
    ("Version", re.compile(r"\bVersion[:\s]+(\d+\.\d+)")),
]


def find_revision(doc):
    """Pull a document revision/version string out of the front matter.

    Args:
        doc: an open fitz.Document.

    Returns:
        a revision string such as "Issue L" or "Version 20250508", or "unknown".
    """
    pages = range(min(cfg.REVISION_SCAN_PAGES, doc.page_count))
    front = "\n".join(doc[i].get_text() for i in pages)
    for label, pat in _REVISION_PATTERNS:
        m = pat.search(front)
        if m:
            return f"{label} {m.group(1)}"
    return "unknown"


# Outlines use chapter, appendix, or bare dotted-number headings.
_NUMBER_PATTERNS = [
    re.compile(r"^Chapter\s+(\d+)\.?\s+(.*)$"),
    re.compile(r"^Appendix\s+([A-Z])\s*[:.]?\s*(.*)$"),
    re.compile(r"^([A-Z]?\d+(?:\.\d+)*)\.?\s+(.*)$"),
]


def parse_section_number(title):
    """Split an outline title into its number and its human name.

    Args:
        title: raw outline title, e.g. "3.1. CSR Field Specifications".

    Returns:
        (section_number, section_name); section_number is None for unnumbered
        front/back matter like "Preface" or "Bibliography".
    """
    title = title.strip()
    for pat in _NUMBER_PATTERNS:
        m = pat.match(title)
        if m:
            return m.group(1), m.group(2).strip()
    # Unnumbered front and back matter are valid sections.
    return None, title


_SECTION_LABEL = r"(?:Chapter|Part|Appendix|Section)\s+"
_NUMBER_ONLY = re.compile(rf"^(?:{_SECTION_LABEL})?([A-Z]?\d+(?:\.\d+)*)\.?$")
_NUMBERED_HEADING = re.compile(rf"^(?:{_SECTION_LABEL})?([A-Z]?\d+(?:\.\d+)*)\.?\s+\S")
_DOT_LEADER = re.compile(r"(?:\.\s*){4,}")


def _styled_lines(page):
    """Return each text line on a page with the type size and weight it was set in.

    Args:
        page: an open fitz.Page.

    Returns:
        list of dicts carrying the line's text, size, boldness, and top edge.
    """
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # type 0 is text; images carry no headings
            continue
        for line in block["lines"]:
            spans = [span for span in line["spans"] if span["text"].strip()]
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not spans or not text:
                continue
            lines.append({
                "text": text,
                "size": max(round(span["size"], 1) for span in spans),
                "bold": any("bold" in span["font"].lower() or span["flags"] & 16
                            for span in spans),
                "top": line["bbox"][1],
            })
    return lines


def _joined_heading_lines(page):
    """Join a section number set on its own line to the title line beneath it.

    Args:
        page: an open fitz.Page.

    Returns:
        Styled lines with split headings such as "A1.2.1" + "Interface" rejoined.
    """
    lines = _styled_lines(page)
    joined, skip = [], False
    for index, line in enumerate(lines):
        if skip:
            skip = False
            continue
        following = lines[index + 1] if index + 1 < len(lines) else None
        if (_NUMBER_ONLY.match(line["text"]) and following
                and abs(following["size"] - line["size"]) < cfg.HEADING_SIZE_SLACK
                and following["bold"] == line["bold"]
                and 0 <= following["top"] - line["top"] < 3 * line["size"]
                and not _NUMBER_ONLY.match(following["text"])):
            joined.append({**line, "text": f"{line['text']} {following['text']}"})
            skip = True
        else:
            joined.append(line)
    return joined


def _body_size(doc, sample=cfg.HEADING_SAMPLE_PAGES):
    """Estimate the document's running body type size.

    Args:
        doc: an open fitz.Document.
        sample: how many pages to sample.

    Returns:
        The type size carrying the most characters, which is the body text.
    """
    step = max(doc.page_count // sample, 1)
    weight = collections.Counter()
    for number in range(0, doc.page_count, step):
        for line in _styled_lines(doc[number]):
            weight[line["size"]] += len(line["text"])
    return weight.most_common(1)[0][0] if weight else 10.0


def _heading_depth(text):
    """Return the outline depth implied by a heading's own numbering.

    Args:
        text: a candidate heading line.

    Returns:
        Depth counting a letter part as its own level, or None when unnumbered.
    """
    match = _NUMBERED_HEADING.match(text) or _NUMBER_ONLY.match(text)
    if not match:
        return None
    number = match.group(1)
    # "A1.2.1" is one level deeper than "1.2.1" because the part letter counts.
    letters = 1 if number[0].isalpha() else 0
    digits = [part for part in number.lstrip(ascii_uppercase).split(".") if part]
    return letters + len(digits)


def detect_headings(doc):
    """Recover an outline from type size and weight for PDFs that ship none.

    Args:
        doc: an open fitz.Document with an empty or missing outline.

    Returns:
        ``(level, title, page)`` triples in the shape ``get_toc`` returns.
    """
    body = _body_size(doc)
    candidates = []
    for number in range(doc.page_count):
        for line in _joined_heading_lines(doc[number]):
            text = line["text"]
            if not 2 <= len(text) <= cfg.HEADING_MAX_CHARS:
                continue
            if text.endswith((",", ";", ":")) or _DOT_LEADER.search(text):
                continue  # trailing punctuation and dot leaders mark prose and contents
            depth = _heading_depth(text)
            distinct = abs(line["size"] - body) > cfg.HEADING_SIZE_SLACK
            if (line["size"] >= body * cfg.HEADING_LARGER
                    or (line["bold"] and (depth or distinct))):
                candidates.append((depth, line["size"], text, number + 1))

    # A heading repeated across the document is a table header, not a heading.
    repeats = collections.Counter(_normalized_heading(item[2]) for item in candidates)
    candidates = [item for item in candidates
                  if repeats[_normalized_heading(item[2])] <= cfg.HEADING_MAX_REPEATS]
    sizes = sorted({item[1] for item in candidates}, reverse=True)
    by_size = {size: rank + 1 for rank, size in enumerate(sizes)}
    return [(depth or by_size[size], text, page)
            for depth, size, text, page in candidates]


def fallback_heading_scores(detected, toc):
    """Score typographic heading detection against a real outline.

    Args:
        detected: ``detect_headings`` output as ``(level, title, page)`` triples.
        toc: The PDF's own outline in the same shape.

    Returns:
        Precision, recall, and F1 of ``(normalized title, page)`` matches.
    """
    found = {(_normalized_heading(title), page) for _, title, page in detected}
    truth = {(_normalized_heading(title), page) for _, title, page in toc}
    hits = len(found & truth)
    precision = hits / len(found) if found else 0.0
    recall = hits / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if hits else 0.0
    return {"fallback_heading_precision": precision, "fallback_heading_recall": recall,
            "fallback_heading_f1": f1}


def _normalized_heading(text):
    """Return a heading's comparison form, ignoring spacing and part labels.

    Args:
        text: a heading line.

    Returns:
        Lowercased text with runs of whitespace collapsed.
    """
    return re.sub(r"\s+", " ", text).strip().lower()


def _subtree_end_page(toc, index, level, page_count):
    """Return the first page after an outline entry's complete subtree.

    Args:
        toc: PDF outline entries as ``(level, title, page)`` triples.
        index: Position of the current entry in ``toc``.
        level: Outline depth of the current entry.
        page_count: Total document pages, used when the subtree reaches the end.

    Returns:
        The first one-based page after the subtree, or ``page_count`` at document end.
    """
    for following_level, _, page in toc[index + 1:]:
        if following_level <= level:
            return page
    return page_count


def _outline_y(doc, page, dest):
    """Return an outline entry's destination height in top-down page coordinates.

    Args:
        doc: an open fitz.Document.
        page: One-based destination page.
        dest: ``get_toc(simple=False)`` destination dict.

    Returns:
        The destination y, 0.0 (page top) when the entry carries no point.
    """
    point = dest.get("to")
    if point is None or not 1 <= page <= doc.page_count:
        return 0.0
    # Named destinations keep PDF's bottom-up coordinates; GOTO ones arrive flipped.
    if dest.get("kind") == fitz.LINK_NAMED:
        point = point * doc[page - 1].transformation_matrix
    return max(point.y, 0.0)


def build_sections(doc, doc_id, doc_name):
    """Turn the PDF outline into inline-linked section and subsection records.

    Args:
        doc: an open fitz.Document.
        doc_id: slug of the document, used to namespace node ids.
        doc_name: basename of the file, carried into every node for provenance.

    Returns:
        Section records in document order, each linked inline to its parent,
        children, and true siblings.
    """
    # Some PDFs ship no outline at all, so recover one from the page typography.
    points = ([(level, title, page, _outline_y(doc, page, dest))
               for level, title, page, dest in doc.get_toc(simple=False)]
              or [(level, title, page, 0.0) for level, title, page in detect_headings(doc)])
    toc = [(level, title, page) for level, title, page, _ in points]
    nodes = []
    for i, (level, title, page, y) in enumerate(points):
        number, name = parse_section_number(title)
        # Own prose ends at the next heading's point; the subtree ends at a peer or ancestor.
        own_end, own_end_y = (points[i + 1][2], points[i + 1][3]) if i + 1 < len(points) \
            else (doc.page_count, None)
        end_page = _subtree_end_page(toc, i, level, doc.page_count)
        nodes.append({
            "node_id": f"{doc_id}::sec::{i:04d}",
            "document_id": doc_id,
            "index": i,
            "level": level,
            "raw_title": title.strip(),
            "section_number": number,
            "section_name": name,
            "doc_name": doc_name,
            "start_page": max(page, 1),
            "end_page": max(end_page, page),
            "own_start_page": max(page, 1),
            "own_end_page": max(own_end, page),
            "start_y": y,
            "own_end_y": own_end_y,
        })

    # Rebuild the hierarchy in one pass by remembering the latest node at each depth.
    last_at_level = {}
    last_sibling = {}
    for node in nodes:
        parent = last_at_level.get(node["level"] - 1)
        node["type"] = "subsection" if parent else "section"
        node["parent_id"] = parent["node_id"] if parent else None

        sibling_key = (node["parent_id"], node["level"])
        earlier = last_sibling.get(sibling_key)
        node["prev_id"] = earlier["node_id"] if earlier else None
        node["next_id"] = None
        if earlier:
            earlier["next_id"] = node["node_id"]
        # Breadcrumbs give both readers and embeddings the section hierarchy.
        crumb = " ".join(x for x in (node["section_number"], node["section_name"]) if x)
        node["path"] = (parent["path"] + [crumb]) if parent else [crumb]
        node["children_ids"] = []
        if parent:
            parent["children_ids"].append(node["node_id"])
        last_at_level[node["level"]] = node
        last_sibling[sibling_key] = node

        node["section_id"] = parent["section_id"] if parent else node["node_id"]
        node["section_title"] = parent["section_title"] if parent else node["section_name"]
        node["subsection_id"] = node["node_id"] if parent else None
        node["subsection_title"] = node["section_name"] if parent else None

        # A new peer or ancestor invalidates remembered descendants.
        for deeper in [k for k in last_at_level if k > node["level"]]:
            del last_at_level[deeper]
    return nodes


def repeated_lines(doc, sample=cfg.FURNITURE_SAMPLE_PAGES):
    """Find running headers and footers so they can be stripped from the text.

    Args:
        doc: an open fitz.Document.
        sample: how many pages to sample when looking for repeats.

    Returns:
        set of line strings that appear on most sampled pages.
    """
    step = max(doc.page_count // sample, 1)
    pages = list(range(0, doc.page_count, step))[:sample]
    counts = {}
    for p in pages:
        for line in set(doc[p].get_text().splitlines()):  # set(), so one page votes once
            line = line.strip()
            if line:
                counts[line] = counts.get(line, 0) + 1
    threshold = len(pages) * cfg.FURNITURE_THRESHOLD
    # Recurring means on two pages at least; one page cannot show a pattern.
    return {line for line, c in counts.items() if c >= max(threshold, 2)}


def furniture_bands(doc):
    """Find the recurring top and bottom edges where running headers and footers sit.

    Args:
        doc: an open fitz.Document.

    Returns:
        ``(top, bottom)``: the topmost block's y0 and bottommost block's y1 that recur on
        at least ``FURNITURE_THRESHOLD`` of voting pages, each None when nothing recurs.
    """
    tops, bottoms = collections.Counter(), collections.Counter()
    for page in doc:
        blocks = [block for block in page.get_text("blocks") if block[6] == 0]
        # A lone block is both topmost and bottommost, so it says nothing about bands.
        if len(blocks) > 1:
            tops[round(min(block[1] for block in blocks))] += 1
            bottoms[round(max(block[3] for block in blocks))] += 1
    threshold = sum(tops.values()) * cfg.FURNITURE_THRESHOLD
    edges = []
    for counts in (tops, bottoms):
        edge, count = counts.most_common(1)[0] if counts else (None, 0)
        # Recurring means on two pages at least; one page cannot show a pattern.
        edges.append(edge if count >= max(threshold, 2) else None)
    return tuple(edges)


def _is_furniture(block, bands):
    """Tell whether one text block sits in a running header or footer band.

    Args:
        block: ``page.get_text("blocks")`` entry.
        bands: ``(top, bottom)`` from ``furniture_bands``.

    Returns:
        True when the block starts at the top band or ends at the bottom band.
    """
    top, bottom = bands
    tolerance = cfg.FURNITURE_BAND_TOLERANCE
    return ((top is not None and block[1] <= top + tolerance)
            or (bottom is not None and block[3] >= bottom - tolerance))


def page_text(page, bands):
    """Return a page's text without its header and footer band blocks.

    Args:
        page: fitz page.
        bands: ``(top, bottom)`` from ``furniture_bands``.

    Returns:
        The page text in ``get_text()`` order, furniture blocks left out.
    """
    return "".join(block[4] for block in page.get_text("blocks")
                   if block[6] == 0 and not _is_furniture(block, bands))


def furniture_counts(doc):
    """Measure what position bands strip and what text matching would still strip.

    Args:
        doc: an open fitz.Document.

    Returns:
        ``furniture_blocks_stripped``: band blocks across all pages;
        ``furniture_text_only_hits``: lines ``repeated_lines`` removes after the bands.
    """
    bands = furniture_bands(doc)
    repeated = repeated_lines(doc)
    stripped = sum(_is_furniture(block, bands) for page in doc
                   for block in page.get_text("blocks") if block[6] == 0)
    text_only = sum(line.strip() in repeated for page in doc
                    for line in page_text(page, bands).splitlines())
    return {"furniture_blocks_stripped": stripped, "furniture_text_only_hits": text_only}


def _clean_pdf_text(raw, furniture):
    """Apply the established PDF furniture and line-wrap cleanup to one text span.

    Args:
        raw: Text extracted from a PDF page or page span.
        furniture: Repeated header and footer lines to remove.

    Returns:
        Cleaned text with layout artefacts removed and line breaks kept.
    """
    kept = []
    for line in raw.splitlines():
        value = line.strip()
        if not value or value in furniture or re.fullmatch(r"\d{1,4}", value):
            continue
        if re.search(r"(?:\.\s*){5,}[ivxlcdm\d]+$", value, re.IGNORECASE):
            continue
        kept.append(value)
    text = "\n".join(kept)
    text = re.sub(r"(?:\.\s*){4,}", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Newlines survive until sentence splitting.
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def is_heading_block(text, title):
    """Tell whether a text block is the printed form of an outline title.

    Args:
        text: Raw block text from ``get_text("blocks")``.
        title: The outline entry's raw title.

    Returns:
        True when the block equals the title once line wraps are joined.
    """
    # A title wrapped after a hyphen ("Floating-\nPoint") keeps its hyphen.
    return " ".join(text.replace("-\n", "-").split()) == " ".join(title.split())


def _in_region(block, regions):
    """Tell whether a text block's centre lies inside one of a page's table bodies.

    Args:
        block: ``get_text("blocks")`` tuple.
        regions: Table body bboxes on the block's page.

    Returns:
        True when the block belongs to a table.
    """
    x, y = (block[0] + block[2]) / 2, (block[1] + block[3]) / 2
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in regions)


def section_page_texts(doc, sec, furniture, bands=(None, None), regions=None):
    """Extract a section as ``(page, text)`` spans cut at outline destination points.

    Args:
        doc: Open PDF document containing the section.
        sec: Section node carrying its start and own-end ``(page, y)`` points.
        furniture: Repeated header and footer lines to remove.
        bands: Header and footer band edges from ``furniture_bands``.
        regions: One-based page to table body bboxes; their blocks are not prose.

    Returns:
        Non-empty ``(one-based page, cleaned text)`` spans in page order, without the
        printed heading block.
    """
    start = (sec["own_start_page"], sec["start_y"])
    end = (sec["own_end_page"], sec["own_end_y"])
    spans = []
    for number in range(sec["own_start_page"], min(sec["own_end_page"], doc.page_count) + 1):
        kept = []
        # The breadcrumb names the section, so its printed heading block is not repeated.
        skip_heading = number == sec["own_start_page"]
        for block in doc[number - 1].get_text("blocks"):
            if (block[6] != 0 or _is_furniture(block, bands)
                    or _in_region(block, (regions or {}).get(number, ()))):
                continue
            # A block belongs to the section whose point sits at or just above its top.
            at = (number, block[1] + cfg.HEADING_POINT_SLACK)
            if at >= start and (end[1] is None or at < end):
                if skip_heading:
                    skip_heading = False
                    if is_heading_block(block[4], sec["raw_title"]):
                        continue
                kept.append(block[4])
        text = _clean_pdf_text("".join(kept), furniture)
        if text:
            spans.append((number, text))
    return spans


# A caption needs whitespace after its separator, so "Table A2.2 shows" never splits.
_CAPTION = re.compile(r"^(Table)\s+([A-Z]?\d+(?:[.-]\d+)*)\s*([.:])\s+(.+)$")

_TABLE_REF = re.compile(r"\bTable\s+([A-Z]?\d+(?:[.-]\d+)*)")
# A table running past its page restates its label in a banner on each following page.
_CONTINUED = re.compile(r"^Table\s+([A-Z]?\d+(?:[.-]\d+)*)\s*[\u2013-]\s*Continued from previous page")
_CONTINUES = "Continued on next page"


def continuation_banners(doc):
    """Find every "Table X – Continued from previous page" banner.

    Args:
        doc: an open fitz.Document.

    Returns:
        dict of label -> list of (one-based page, banner bbox), in page order.
    """
    banners = collections.defaultdict(list)
    for i in range(doc.page_count):
        for block in doc[i].get_text("blocks"):
            match = _CONTINUED.match(block[4].strip())
            if match:
                banners[match.group(1)].append((i + 1, tuple(block[:4])))
    return banners


def build_caption_index(doc):
    """Find every table caption in the document.

    Args:
        doc: an open fitz.Document.

    Returns:
        dict of label -> {page, caption, bbox}, one entry per table.
    """
    index = {}
    for i in range(doc.page_count):
        for block in doc[i].get_text("blocks"):
            body = block[4].strip()
            if not body:
                continue
            m = _CAPTION.match(body.splitlines()[0].strip())
            if m and m.group(2) not in index:
                index[m.group(2)] = {
                    "page": i + 1,
                    "caption": " ".join(m.group(4).split()),
                    "bbox": tuple(block[:4]),
                }
    return index


def table_region(page, caption_bbox):
    """Work out the rectangle holding a table's body.

    Args:
        page: the fitz.Page the caption sits on.
        caption_bbox: bounding box of the caption block.

    Returns:
        a fitz.Rect covering the table, or None if nothing follows the caption.
    """
    # The first row's left edge is reliable across wide ARM and narrow RISC-V tables.
    blocks = sorted(page.get_text("blocks"), key=itemgetter(1))
    below = [b for b in blocks if b[1] > caption_bbox[3]]
    if not below:
        return None
    edge = below[0][0]
    bottom = page.rect.height
    for b in below[1:]:
        first = b[4].strip().splitlines()[0].strip() if b[4].strip() else ""
        if b[0] < edge - cfg.TABLE_EDGE_SLACK or _CAPTION.match(first):
            bottom = b[1]
            break
    top = caption_bbox[3]
    return (
        fitz.Rect(0, top, page.rect.width, bottom)
        if bottom > top + cfg.MIN_TABLE_HEIGHT else None
    )


def rows_by_alignment(page, region):
    """Recover table rows from column alignment alone, for tables with no ruling lines.

    Args:
        page: the fitz.Page holding the table.
        region: fitz.Rect covering the table body.

    Returns:
        list of rows, each a list of cell strings; empty if no header could be found.
    """
    words = [w for w in page.get_text("words") if region.y0 <= w[1] < region.y1]
    if not words:
        return []

    # Group by visual line first so wrapped cells retain reading order.
    lines = collections.defaultdict(list)
    for w in words:
        lines[round(w[1], 1)].append(w)
    ordered = [sorted(lines[y], key=itemgetter(0)) for y in sorted(lines)]

    # Nearby header words belong to one multi-word column heading.
    columns = []
    for w in ordered[0]:
        if not columns or w[0] - columns[-1] > cfg.COLUMN_GAP:
            columns.append(w[0])
    if len(columns) < 2:
        return []  # a single column is not a table, it is a paragraph

    # A word in the leftmost column starts the next row.
    rows = []
    for line in ordered:
        starts_row = line[0][0] < columns[0] + cfg.COLUMN_SLACK
        if starts_row or not rows:
            rows.append([[] for _ in columns])
        for w in line:
            # Clamp hanging row labels into column zero rather than dropping them.
            fits = [j for j, c in enumerate(columns) if w[0] >= c - cfg.COLUMN_SLACK]
            i = max(fits) if fits else 0
            rows[-1][i].append(w[4])
    return [[" ".join(cell) for cell in row] for row in rows]


def unstack_row(row):
    """Split one extracted row back into the several rows it really holds.

    Args:
        row: list of cell strings, possibly holding embedded newlines.

    Returns:
        list of rows; a single-element list when there was nothing to split.
    """
    # Equal newline depths indicate several logical rows collapsed into each cell.
    parts = [c.split("\n") for c in row]
    counts = {len(p) for p in parts if len(p) > 1}
    if len(counts) != 1:
        return [[c.replace("\n", " ").strip() for c in row]]
    depth = counts.pop()
    return [[(p[i].strip() if len(p) > 1 else p[0].strip()) for p in parts] for i in range(depth)]


def _ruled_tables(page):
    """Return the page's ruled tables that are large enough to be real tables.

    Args:
        page: the fitz.Page to scan.

    Returns:
        list of pymupdf Table objects with at least two rows and two columns.
    """
    return [table for table in page.find_tables(strategy="lines").tables
            if table.row_count > 1 and table.col_count > 1]


def nearest_ruled_table(page, caption_bbox):
    """Return the ruled table nearest the caption, searching both sides of it.

    Args:
        page: the fitz.Page holding the caption.
        caption_bbox: bounding box of the caption block.

    Returns:
        The closest qualifying Table, or None when the caption stands alone.
    """
    top, bottom = caption_bbox[1], caption_bbox[3]
    best = None
    for table in _ruled_tables(page):
        if table.bbox[1] >= bottom - cfg.CAPTION_SLACK:
            gap, above = table.bbox[1] - bottom, False
        elif table.bbox[3] <= top + cfg.CAPTION_SLACK:
            gap, above = top - table.bbox[3], True
        else:
            continue  # the caption sits inside this table, so it is not its caption
        # Captions lead their table more often than they follow, so ties go below.
        key = (gap, above)
        if gap <= cfg.CAPTION_MAX_GAP and (best is None or key < best[0]):
            best = (key, table)
    return best[1] if best else None


def _table_rows(table):
    """Flatten one pymupdf table into cleaned, unstacked rows.

    Args:
        table: a pymupdf Table object.

    Returns:
        list of row lists with collapsed multi-row cells split back apart.
    """
    rows = [[(cell or "").strip() for cell in row] for row in table.extract()]
    return [out for row in rows for out in unstack_row(row)]


def extract_table(page, caption_bbox, next_page=None):
    """Pull the cell contents of one table off the page.

    Args:
        page: the fitz.Page holding the table.
        caption_bbox: bounding box of the table's caption block.
        next_page: following fitz.Page, used when a caption ends its own page.

    Returns:
        (rows, method, region) where rows is a list of row lists, method names the
        strategy that worked, and region is ``(page offset, bbox)`` of the table body
        (offset 1 when it sits on ``next_page``); rows is empty and region None if no
        strategy found anything.
    """
    # Prefer ruled extraction because RISC-V's per-column blocks defeat alignment.
    table = nearest_ruled_table(page, caption_bbox)
    if table is not None:
        rows = _table_rows(table)
        if len(rows) > 1:
            return rows, "lines", (0, tuple(table.bbox))

    # A caption low on the page introduces a table that starts on the next one.
    if next_page is not None and caption_bbox[3] > page.rect.height * cfg.CAPTION_PAGE_BREAK:
        carried = _ruled_tables(next_page)
        if carried:
            first = min(carried, key=lambda item: item.bbox[1])
            rows = _table_rows(first)
            if len(rows) > 1:
                return rows, "lines_page_break", (1, tuple(first.bbox))

    # Fall back to alignment for unruled ARM tables.
    region = table_region(page, caption_bbox)
    if region is None:
        return [], "none", None
    rows = rows_by_alignment(page, region)
    return ((rows, "alignment", (0, tuple(region))) if len(rows) > 1
            else ([], "none", None))


def rows_to_markdown(rows):
    """Render extracted rows as a markdown table.

    Args:
        rows: list of row lists, the first of which is treated as the header.

    Returns:
        a markdown table string.
    """
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(padded[0]) + " |", "|" + "---|" * width]
    for r in padded[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def table_row_windows(columns, rows, max_chars=cfg.MAX_TABLE_CHUNK_CHARS):
    """Split a long table's rows into windows that each restate the header row.

    Args:
        columns: The table's header cells, repeated into every window.
        rows: The table's data rows, in source order.
        max_chars: Soft character cap per window, header included.

    Returns:
        List of ``(row_start, window_rows)`` pairs covering every row exactly once.
    """
    windows, start = [], 0
    while start < len(rows):
        end = start
        while end < len(rows):
            # Measure the real rendering, so the cap can never drift from the markdown.
            if end > start and len(rows_to_markdown([columns] + rows[start:end + 1])) > max_chars:
                break
            end += 1
        # Always admit one row so a single oversized row cannot stall the loop.
        windows.append((start, rows[start:max(end, start + 1)]))
        start = max(end, start + 1)
    # Rows never flow into each other the way sentences do, so windows do not overlap.
    return windows or [(0, [])]


def build_tables(doc, doc_id, doc_name, nodes):
    """Extract every captioned table and attach it to the section that contains it.

    Args:
        doc: an open fitz.Document.
        doc_id: slug of the document, used to namespace node ids.
        doc_name: basename of the file, carried into every table node.
        nodes: The inline-linked hierarchy records from ``build_sections``.

    Returns:
        Table metadata keyed by label. Tables are not hierarchy nodes.
    """
    tables = {}
    banners = continuation_banners(doc)
    for label, cap in build_caption_index(doc).items():
        page = doc[cap["page"] - 1]
        following = doc[cap["page"]] if cap["page"] < doc.page_count else None
        rows, method, region = extract_table(page, cap["bbox"], next_page=following)
        regions = [(cap["page"] + region[0], region[1])] if region else []
        # Continuation pages restate the header row, which is dropped once rows exist.
        for number, banner in banners.get(label, []):
            carried, _, carried_region = extract_table(doc[number - 1], banner)
            carried = [row for row in carried if " ".join(row).strip() != _CONTINUES]
            if not carried:
                continue
            rows = rows + (carried[1:] if rows and carried[0] == rows[0] else carried)
            regions.append((number + carried_region[0], carried_region[1]))
        # The owner is the last section whose heading point sits at or above the caption.
        caption_at = (cap["page"], cap["bbox"][1] + cfg.HEADING_POINT_SLACK)
        owner = None
        for node in nodes:
            if (node["own_start_page"], node["start_y"] or 0.0) <= caption_at:
                owner = node
            else:
                break
        columns = list(rows[0]) if rows else []
        data_rows = [list(row) for row in rows[1:]] if rows else []
        tables[label] = {
            "table_id": f"{doc_id}::table::{label}",
            "document_id": doc_id,
            "doc_name": doc_name,
            "label": label,
            "caption": cap["caption"],
            "page": cap["page"],
            "hierarchy_id": owner["node_id"] if owner else None,
            "section_id": owner["section_id"] if owner else None,
            "section_title": owner["section_title"] if owner else None,
            "subsection_id": owner["subsection_id"] if owner else None,
            "subsection_title": owner["subsection_title"] if owner else None,
            "section_number": owner["section_number"] if owner else None,
            "section_path": owner["path"] if owner else [],
            "columns": columns,
            "rows": data_rows,
            "n_rows": len(data_rows),
            "extraction": method,
            # One-based page and bbox of every body part, so prose can leave them out.
            "regions": regions,
            "markdown": rows_to_markdown(rows) if rows else "",
        }
    return tables


def find_table_refs(text, tables):
    """Resolve every "see Table X" mention in a piece of text to its table id.

    Args:
        text: the chunk body to scan.
        tables: The document's structured table metadata, keyed by label.

    Returns:
        Table ids in first-mention order; labels with no table in this document are dropped.
    """
    return [tables[label]["table_id"] for label in dict.fromkeys(_TABLE_REF.findall(text))
            if label in tables]


def enrich_spec_record(record):
    """Append the common deterministic provenance contract to one chunk.

    Args:
        record: Mutable prose or table chunk record to enrich in place.

    Returns:
        The same record with hierarchy, table, page, and source metadata.
    """
    text = record.get("text", "")
    chunk_type = record.get("chunk_type")
    is_table = chunk_type == "table"
    document_id = record.get("document_id") or record["chunk_id"].split("#", 1)[0]
    own_table_id = record.get("table_id") if is_table else None
    table_ids = list(dict.fromkeys([
        *record.get("table_ids", []),
        *([own_table_id] if own_table_id else []),
    ]))
    section_title = record.get("section_title") or record.get("section_name")
    record.update({
        "record_id": f"spec::chunk::{record['chunk_id']}",
        "source_id": (own_table_id if is_table else
                      record.get("hierarchy_id") or record.get("subsection_id")
                      or record.get("section_id")),
        "document_id": document_id,
        "section_id": record.get("section_id"),
        "section_title": section_title,
        "subsection_id": record.get("subsection_id"),
        "subsection_title": record.get("subsection_title"),
        "page": record.get("page", record.get("start_page")),
        "table_ids": table_ids,
        "source_file": record.get("source_file") or record.get("doc_name"),
        "content_hash": content_hash(record.get("context_header", ""), text),
    })
    return record


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text):
    """Split text into sentences with a rule-based splitter.

    Args:
        text: cleaned section text.

    Returns:
        list of sentence strings, whitespace-normalised, empties dropped.
    """
    # Keep this stage offline; overlap absorbs occasional regex miscuts of spec tokens.
    parts = _SENTENCE_END.split(text)
    sentences = []
    for part in parts:
        part = " ".join(part.split())
        if not part:
            continue
        # Abbreviations and bare initials/digits are false sentence endings.
        if sentences:
            tail = sentences[-1].rstrip(".")
            last_word = tail.split()[-1] if tail.split() else ""
            if last_word in cfg.ABBREVIATIONS or re.fullmatch(r"[A-Z0-9]", last_word):
                sentences[-1] = sentences[-1] + " " + part
                continue
        sentences.append(part)
    return sentences


def sliding_window_chunks(sentences, max_chars=cfg.MAX_CHUNK_CHARS, overlap=cfg.OVERLAP_SENTENCES):
    """Pack sentences into chunks, sliding back a few sentences between chunks.

    Args:
        sentences: list of sentences from one section.
        max_chars: soft character cap per chunk.
        overlap: how many trailing sentences of chunk N also open chunk N+1.

    Returns:
        list of chunks, each a list of sentences.
    """
    chunks = []
    i = 0
    while i < len(sentences):
        j = i
        size = 0
        while j < len(sentences):
            value = sentences[j][0] if isinstance(sentences[j], tuple) else sentences[j]
            # Always admit the first sentence so an oversized sentence cannot stall.
            if j > i and size + len(value) > max_chars:
                break
            size += len(value) + 1
            j += 1
        chunks.append(sentences[i:j])
        if j >= len(sentences):
            break
        # Reopen the next chunk with the configured trailing overlap.
        i = max(j - overlap, i + 1)
    return chunks


def ingest_pdf(path):
    """Turn one PDF into inline-linked sections, structured tables, and chunks.

    Args:
        path: path to the PDF.

    Returns:
        ``(sections, tables, records)`` for this document.
    """
    doc = fitz.open(path)
    doc_name = path.name
    doc_id = slugify(doc_name)
    revision = find_revision(doc)
    furniture = repeated_lines(doc)
    bands = furniture_bands(doc)

    # Build structure first so every chunk inherits deterministic hierarchy metadata.
    nodes = build_sections(doc, doc_id, doc_name)
    tables = build_tables(doc, doc_id, doc_name, nodes)
    regions = collections.defaultdict(list)
    for table in tables.values():
        for page, bbox in table["regions"]:
            regions[page].append(bbox)

    records = []
    for sec in nodes:
        if sec["section_name"].strip().lower() in cfg.SKIP_TITLES:
            continue
        page_texts = section_page_texts(doc, sec, furniture, bands, regions)
        if not page_texts:
            continue
        # Breadcrumbs disambiguate otherwise context-free paragraphs for embedding.
        header = f"{doc_name} > " + " > ".join(sec["path"])
        sentences = [(sentence, page) for page, page_text in page_texts
                     for sentence in split_sentences(page_text)]
        for k, chunk in enumerate(sliding_window_chunks(sentences)):
            body = " ".join(sentence for sentence, _ in chunk)
            chunk_pages = [page for _, page in chunk]
            # Deterministic IDs support stable vector-store updates.
            records.append(enrich_spec_record({
                "chunk_id": f"{doc_id}#{sec['index']:04d}#{k:03d}",
                "chunk_type": "prose",
                "document_id": doc_id,
                "doc_name": doc_name,
                "source_file": doc_name,
                "revision": revision,
                "hierarchy_id": sec["node_id"],
                "section_id": sec["section_id"],
                "section_title": sec["section_title"],
                "subsection_id": sec["subsection_id"],
                "subsection_title": sec["subsection_title"],
                "section_number": sec["section_number"],
                "section_name": sec["section_name"],
                "section_path": sec["path"],
                "context_header": header,
                "parent_id": sec["parent_id"],
                # Cite chunk pages, not the wider section span.
                "start_page": min(chunk_pages),
                "end_page": max(chunk_pages),
                "page": min(chunk_pages),
                "chunk_index": k,
                "table_ids": find_table_refs(body, tables),
                "text": body,
            }))

    # Window long tables by row, because the encoder truncates an oversized one silently.
    for table in tables.values():
        if not table["markdown"]:
            continue
        windows = table_row_windows(table["columns"], table["rows"])
        for start, window in windows:
            markdown = rows_to_markdown([table["columns"]] + window)
            # Unsplit tables keep their bare id so their chunk ids stay stable.
            suffix = f"#{start:04d}" if len(windows) > 1 else ""
            records.append(enrich_spec_record({
                "chunk_id": f"{doc_id}#table#{table['label']}{suffix}",
                "chunk_type": "table",
                "document_id": doc_id,
                "doc_name": doc_name,
                "source_file": doc_name,
                "revision": revision,
                "hierarchy_id": table["hierarchy_id"],
                "section_id": table["section_id"],
                "section_title": table["section_title"],
                "subsection_id": table["subsection_id"],
                "subsection_title": table["subsection_title"],
                "section_number": table["section_number"],
                "section_name": table["subsection_title"] or table["section_title"],
                "section_path": table["section_path"],
                "context_header": " > ".join([
                    doc_name, *table["section_path"],
                    f"Table {table['label']}: {table['caption']}"]),
                "table_label": table["label"],
                "table_id": table["table_id"],
                "table_ids": [table["table_id"]],
                "caption": table["caption"],
                "start_page": table["page"],
                "end_page": table["page"],
                "page": table["page"],
                "columns": table["columns"],
                "rows": window,
                "n_rows": len(window),
                "row_start": start,
                "table_rows": table["n_rows"],
                "extraction": table["extraction"],
                "text": markdown,
            }))

    # Document role needs the whole document, so it is stamped once every chunk exists.
    document_scores = roles.document_role_scores(record["text"] for record in records)
    for record in records:
        record["document_role_scores"] = document_scores

    doc.close()
    return nodes, tables, records


def hierarchy_document(path, revision, page_count, sections, furniture=None):
    """Build one document record containing only its inline-linked section hierarchy.

    Args:
        path: Source PDF path.
        revision: Deterministically extracted document revision.
        page_count: Number of pages in the source PDF.
        sections: Inline-linked outline records belonging to the document.
        furniture: Optional ingest report (furniture counts, fallback heading scores).

    Returns:
        A document record with its complete section list nested inline.
    """
    document_id = slugify(path.name)
    roots = [section["node_id"] for section in sections if section["parent_id"] is None]
    return {
        "document_id": document_id,
        "type": "document",
        "title": path.stem,
        "source_file": path.name,
        "revision": revision,
        "page_count": page_count,
        "root_section_ids": roots,
        **(furniture or {}),
        "sections": sections,
        "content_hash": content_hash(
            document_id, revision,
            json.dumps([(section["node_id"], section["raw_title"],
                         section["start_page"], section["end_page"])
                        for section in sections], ensure_ascii=False)),
    }


def main():
    """Ingest every PDF and write chunks plus the hierarchy-only artifact.

    Returns:
        None.
    """
    paths = sorted(cfg.INGEST_DIR.glob("*.pdf"))
    if not paths:
        raise SystemExit(f"no PDFs found in {cfg.INGEST_DIR}")
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_records = []
    documents = []
    for path in paths:
        nodes, tables, records = ingest_pdf(path)
        revision = records[0]["revision"] if records else "unknown"
        all_records.extend(records)
        with fitz.open(path) as document:
            report = furniture_counts(document)
            if document.get_toc():  # validate the no-outline fallback where truth exists
                report.update(fallback_heading_scores(
                    detect_headings(document), document.get_toc()))
            documents.append(hierarchy_document(path, revision, document.page_count, nodes,
                                                report))
        prose = [r for r in records if r["chunk_type"] == "prose"]
        avg = sum(len(r["text"]) for r in prose) // max(len(prose), 1)
        n_tab = len(records) - len(prose)
        print(f"{path.name:<28} sections={len(nodes):<5} tables={n_tab:<4} "
              f"chunks={len(records):<6} avg_chars={avg}")

    hierarchy_temporary = cfg.SPEC_HIERARCHY_PATH.with_suffix(".json.tmp")
    with open(hierarchy_temporary, "w", encoding="utf-8") as f:
        json.dump({"schema_version": SCHEMA_VERSION, "documents": documents},
                  f, ensure_ascii=False, indent=1)
    hierarchy_temporary.replace(cfg.SPEC_HIERARCHY_PATH)
    chunks_temporary = cfg.CHUNKS_PATH.with_suffix(".jsonl.tmp")
    with open(chunks_temporary, "w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    chunks_temporary.replace(cfg.CHUNKS_PATH)
    rel = cfg.OUTPUT_DIR.relative_to(cfg.ROOT)
    section_count = sum(len(document["sections"]) for document in documents)
    print(f"====  wrote {len(documents)} documents and {section_count} sections to "
          f"{rel / cfg.SPEC_HIERARCHY_PATH.name}")
    print(f"====  wrote {len(all_records)} chunks to {rel / cfg.CHUNKS_PATH.name}")


if __name__ == "__main__":
    main()
