"""Ingest books through local Qianfan-OCR Markdown, without image evidence.

Run ``python src/ocr_ingest.py --output-dir output/ocr_candidate``. Production
callers keep using ``ingest.py`` until a complete benchmark demonstrates a gain.

Flow: Render PDF pages -> cache local Qianfan Markdown -> parse headings/tables -> reuse chunk validation -> write scratch output.
"""

import argparse
import collections
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from markdown_it import MarkdownIt

import configure as cfg
import ingest

MODEL = "baidu/Qianfan-OCR"
MODEL_REVISION = "623bf5d20d446abdb36606aa4547cd0c18886fe5"
PROMPT = "Parse this document to Markdown."
MAX_NEW_TOKENS = 16384
_CAPTION = re.compile(
    r"^Table\s+([A-Z]?\d+(?:[.\-]\d+)*)\s*[:.\u2013\u2014-]\s+(.+)$", re.I)
_CONTINUED = re.compile(r"^Table\s+([A-Z]?\d+(?:[.\-]\d+)*)\b.*\bcontinued\b", re.I)


class TableHTML(HTMLParser):
    """Expand HTML table spans into the rectangular rows downstream extractors read."""

    def __init__(self):
        """Start an empty grid and text accumulator."""
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.spans = {}
        self.cells = None
        self.rowspan = self.colspan = 1

    def handle_starttag(self, tag, attrs):
        """Track row boundaries, cells, spans and explicit line breaks."""
        if tag == "tr":
            self.rows.append([])
        elif tag in {"th", "td"}:
            if not self.rows:
                self.rows.append([])
            attributes = dict(attrs)
            self.rowspan = min(max(int(attributes.get("rowspan", 1)), 1), 1000)
            self.colspan = min(max(int(attributes.get("colspan", 1)), 1), 1000)
            self.cells = []
        elif tag == "br" and self.cells is not None:
            self.cells.append(" ")

    def handle_data(self, data):
        """Collect visible cell text, decoding HTML entities through HTMLParser."""
        if self.cells is not None:
            self.cells.append(data)

    def handle_endtag(self, tag):
        """Place each cell and its spans, copying merged labels into covered cells."""
        if tag not in {"td", "th"} or self.cells is None:
            return
        row_number = len(self.rows) - 1
        row = self.rows[-1]
        while (row_number, len(row)) in self.spans:
            row.append(self.spans.pop((row_number, len(row))))
        column = len(row)
        value = " ".join("".join(self.cells).split())
        row.extend([value] * self.colspan)
        for offset in range(1, self.rowspan):
            for col in range(column, column + self.colspan):
                self.spans[row_number + offset, col] = value
        self.cells = None

    def grid(self):
        """Finish trailing rowspans and return nonempty rows padded to one width."""
        for index, row in enumerate(self.rows):
            while (index, len(row)) in self.spans:
                row.append(self.spans.pop((index, len(row))))
        width = max(map(len, self.rows), default=0)
        return [row + [""] * (width - len(row)) for row in self.rows if any(row)]


def inline_text(token):
    """Read Markdown text while excluding images and retaining code and formulas."""
    return "".join(
        "\n" if child.type in {"softbreak", "hardbreak"} else child.content
        for child in token.children or []
        if child.type in {"text", "code_inline", "softbreak", "hardbreak"})


def markdown_blocks(pages):
    """Detect headings, pipe/HTML tables and captions from page Markdown strings."""
    parser = MarkdownIt("commonmark").enable("table")
    blocks = []
    for page, markdown in enumerate(pages, 1):
        tokens = parser.parse(markdown)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            block = {"page": page, "line": token.map[0] if token.map else 0}
            if token.type == "table_open":
                rows = []
                index += 1
                while tokens[index].type != "table_close":
                    if tokens[index].type == "tr_open":
                        rows.append([])
                    elif tokens[index].type == "inline":
                        rows[-1].append(inline_text(tokens[index]).replace("\n", " "))
                    index += 1
                blocks.append({**block, "kind": "table", "rows": rows})
            elif token.type == "html_block":
                # Qianfan emits HTML for complex tables even in Markdown output.
                for html in re.split(r"(<table\b.*?</table\s*>)", token.content, flags=re.I | re.S):
                    if re.match(r"<table\b", html, re.I):
                        table = TableHTML()
                        table.feed(html)
                        blocks.append({**block, "kind": "table", "rows": table.grid()})
                    else:
                        for paragraph in re.split(r"</(?:p|div|h[1-6])\s*>", html, flags=re.I):
                            text = unescape(re.sub(r"<[^>]*>", " ", paragraph)).strip()
                            if text:
                                blocks.append({**block, "kind": "text", "text": text})
            elif token.type == "heading_open":
                blocks.append({**block, "kind": "heading", "level": int(token.tag[1:]),
                               "text": inline_text(tokens[index + 1])})
                index += 2
            elif token.type == "inline":
                blocks.append({**block, "kind": "text", "text": inline_text(token)})
            elif token.type in {"fence", "code_block"}:
                blocks.append({**block, "kind": "code", "text": token.content})
            index += 1
    for block in blocks:
        if block["kind"] not in {"heading", "text"}:
            continue
        text = block["text"].strip()
        continued = _CONTINUED.match(text)
        caption = _CAPTION.fullmatch(text)
        if continued or caption:
            block.update(kind="caption", label=(continued or caption).group(1),
                         caption=caption.group(2) if caption and not continued else "",
                         continued=bool(continued))
    return blocks


def ingest_markdown(pages, path):
    """Build the existing chunk contract solely from page-preserving OCR Markdown."""
    if not pages:
        raise ValueError("OCR returned no pages")
    blocks = markdown_blocks(pages)
    doc_id = ingest.slugify(path.name)
    # Markdown authors and OCR models may skip heading levels; keep the nearest parent.
    stack, points = [], []
    for block in blocks:
        if block["kind"] != "heading":
            continue
        while stack and stack[-1] >= block["level"]:
            stack.pop()
        stack.append(block["level"])
        points.append((len(stack), block["text"], block["page"], block["line"]))
    if not points or (points[0][2], points[0][3]) > (1, 0):
        points.insert(0, (1, "Front matter", 1, 0))
    nodes = ingest.section_nodes(points, doc_id, path.name, len(pages))
    texts = collections.defaultdict(list)
    tables = {}
    previous_table = None
    section_index = 0
    for index, block in enumerate(blocks):
        position = (block["page"], block["line"])
        while (section_index + 1 < len(nodes)
               and (nodes[section_index + 1]["start_page"],
                    nodes[section_index + 1]["start_y"]) <= position):
            section_index += 1
        owner = nodes[section_index]
        if block["kind"] in {"heading", "caption"}:
            continue
        if block["kind"] != "table":
            if block["text"].strip():
                texts[owner["node_id"]].append((block["page"], block["text"]))
            continue
        neighbours = blocks[max(0, index - 1):index] + blocks[index + 1:index + 2]
        caption = next((item for item in neighbours if item["kind"] == "caption"), None)
        label = caption["label"] if caption else f"unlabelled-{len(tables) + 1}"
        rows = block["rows"]
        if not rows:
            continue
        if caption is None and previous_table is not None:
            previous_index, previous_label = previous_table
            previous = tables[previous_label]
            if (rows[0] == previous["columns"]
                    and block["page"] == previous["end_page"] + 1
                    and all(item["kind"] == "caption" or not item.get("text", "").strip()
                            for item in blocks[previous_index + 1:index])):
                label = previous_label
        if label in tables:
            previous = tables[label]
            data = rows[1:] if rows[0] == previous["columns"] else rows
            previous["rows"].extend(data)
            previous["n_rows"] = len(previous["rows"])
            previous["end_page"] = block["page"]
            previous["markdown"] = ingest.rows_to_markdown([previous["columns"], *previous["rows"]])
            previous_table = (index, label)
            continue
        tables[label] = {
            "table_id": f"{doc_id}::table::{label}", "label": label,
            "caption": caption["caption"] if caption else "Unlabelled table",
            "page": block["page"], "end_page": block["page"],
            "hierarchy_id": owner["node_id"], "section_number": owner["section_number"],
            "section_title": owner["section_title"],
            "subsection_title": owner["subsection_title"], "section_path": owner["path"],
            "columns": rows[0], "rows": rows[1:], "n_rows": len(rows) - 1,
            "extraction": "qianfan_markdown", "markdown": ingest.rows_to_markdown(rows),
        }
        previous_table = (index, label)
    front = "\n".join(pages[:cfg.REVISION_SCAN_PAGES])
    revision = next((f"{label} {match.group(1)}" for label, pattern in ingest._REVISION_PATTERNS
                     if (match := pattern.search(front))), "unknown")
    records = ingest.section_records(path, revision, nodes, tables, texts)
    for record in records:
        if record["chunk_type"] == "table":
            record["end_page"] = tables[record["table_label"]]["end_page"]
    return nodes, tables, records


class QianfanOCR:
    """Run the pinned open-weight model locally, one rendered PDF page at a time."""

    def __init__(self, model=MODEL, revision=MODEL_REVISION, four_bit=False):
        """Load native Transformers weights; optional NF4 fits smaller CUDA GPUs."""
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

        options = {"revision": revision, "dtype": torch.bfloat16, "device_map": "auto"}
        if four_bit:
            options["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
        self.model = AutoModelForImageTextToText.from_pretrained(model, **options).eval()
        self.processor = AutoProcessor.from_pretrained(model, revision=revision)
        self.identity = {"model": model, "revision": revision, "four_bit": four_bit,
                         "prompt": PROMPT, "max_new_tokens": MAX_NEW_TOKENS, "dpi": 144}

    def recognize(self, image):
        """Generate complete Markdown, failing rather than caching truncated pages."""
        import torch

        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": PROMPT}]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True,
            return_tensors="pt", enable_thinking=False).to(self.model.device)
        with torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        generated = output[:, inputs["input_ids"].shape[1]:]
        if generated.shape[1] >= MAX_NEW_TOKENS:
            raise ValueError("OCR page reached max_new_tokens; refusing truncated Markdown")
        markdown = self.processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
        if not markdown:
            raise ValueError("OCR returned empty Markdown")
        return markdown


def ocr_pages(path, engine, cache_dir):
    """Cache Markdown per source hash and model settings, retaining every page number."""
    import fitz
    from PIL import Image

    identity = {**engine.identity, "pdf_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    directory = cache_dir / f"{ingest.slugify(path.name)}-{key}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    pages = []
    with fitz.open(path) as document:
        for number, page in enumerate(document, 1):
            cached = directory / f"{number:05d}.md"
            if not cached.exists():
                pixmap = page.get_pixmap(dpi=144, alpha=False, colorspace=fitz.csRGB)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                markdown = engine.recognize(image)
                temporary = cached.with_suffix(".md.tmp")
                temporary.write_text(markdown, encoding="utf-8")
                temporary.replace(cached)
                print(f"{path.name}: OCR page {number}/{len(document)}", flush=True)
            pages.append(cached.read_text(encoding="utf-8"))
    return pages


def main():
    """Write candidate chunks and hierarchy to an isolated, explicitly selected directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=cfg.OUTPUT_DIR / "ocr_candidate")
    parser.add_argument("--cache-dir", type=Path, default=cfg.OUTPUT_DIR / "ocr_markdown")
    parser.add_argument("--four-bit", action="store_true")
    args = parser.parse_args()
    paths = sorted(cfg.INGEST_DIR.glob("*.pdf"))
    if not paths:
        raise SystemExit(f"no PDFs found in {cfg.INGEST_DIR}")
    engine = QianfanOCR(four_bit=args.four_bit)
    records, documents = [], []
    for path in paths:
        pages = ocr_pages(path, engine, args.cache_dir)
        nodes, tables, chunks = ingest_markdown(pages, path)
        records.extend(chunks)
        revision = chunks[0]["revision"] if chunks else "unknown"
        documents.append(ingest.hierarchy_document(path, revision, len(pages), nodes))
        print(f"{path.name}: sections={len(nodes)} tables={len(tables)} chunks={len(chunks)}",
              flush=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in {
        "chunks.jsonl": "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        "spec_hierarchy.json": json.dumps({"schema_version": ingest.SCHEMA_VERSION,
                                          "documents": documents}, ensure_ascii=False, indent=1),
    }.items():
        target = args.output_dir / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)


if __name__ == "__main__":
    main()
