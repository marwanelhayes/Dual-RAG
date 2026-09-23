"""Hermetic tests for hierarchy-only specification ingestion."""

from pathlib import Path

import configure as cfg
import ingest


class PageSequence(list):
    """Minimal PDF stand-in that indexes like pages and reports its length."""

    def __init__(self, page_count):
        """Fill the sequence with one placeholder page per document page."""
        super().__init__(object() for _ in range(page_count))
        self.page_count = page_count


class OutlineDocument:
    """Minimal PDF-outline stand-in for hierarchy construction tests."""

    page_count = 20

    def get_toc(self):
        """Return a deterministic two-level outline."""
        return [
            (1, "1 Floating Point Unit", 2),
            (2, "1.1 Interface signals", 3),
            (2, "1.2 Rounding", 5),
            (1, "2 Exceptions", 8),
        ]


def test_outline_builds_inline_document_section_subsection_hierarchy_only():
    sections = ingest.build_sections(OutlineDocument(), "fpu_spec", "fpu.pdf")
    document = ingest.hierarchy_document(
        Path("fpu.pdf"), "Revision A", OutlineDocument.page_count, sections)

    assert document["type"] == "document"
    assert document["root_section_ids"] == [
        "fpu_spec::sec::0000", "fpu_spec::sec::0003"]
    assert document["sections"] is sections
    assert {section["type"] for section in sections} == {"section", "subsection"}
    assert sections[0]["children_ids"] == [
        "fpu_spec::sec::0001", "fpu_spec::sec::0002"]
    assert sections[1]["parent_id"] == sections[0]["node_id"]
    assert sections[1]["section_id"] == sections[0]["node_id"]
    assert sections[1]["subsection_id"] == sections[1]["node_id"]
    assert sections[1]["next_id"] == sections[2]["node_id"]
    assert "nodes" not in document and "edges" not in document


def test_spec_chunk_contract_contains_every_required_provenance_field():
    record = ingest.enrich_spec_record({
        "chunk_id": "axi_spec#0001#000",
        "chunk_type": "prose",
        "document_id": "axi_spec",
        "doc_name": "AXI spec.pdf",
        "source_file": "AXI spec.pdf",
        "context_header": "AXI > Write address",
        "hierarchy_id": "axi_spec::sec::0001",
        "section_id": "axi_spec::sec::0000",
        "section_title": "Channels",
        "subsection_id": "axi_spec::sec::0001",
        "subsection_title": "Write address",
        "start_page": 12,
        "end_page": 12,
        "tables_cited": [{
            "label": "A3.2", "table_id": "axi_spec::table::A3.2",
            "resolved": True,
        }],
        "text": "AWVALID remains asserted until AWREADY.",
    })

    assert record["record_id"] == "spec::chunk::axi_spec#0001#000"
    assert record["document_id"] == "axi_spec"
    assert record["section_id"] == "axi_spec::sec::0000"
    assert record["section_title"] == "Channels"
    assert record["subsection_id"] == "axi_spec::sec::0001"
    assert record["subsection_title"] == "Write address"
    assert record["page"] == 12
    assert record["table_ids"] == ["axi_spec::table::A3.2"]
    assert record["source_file"] == "AXI spec.pdf"
    assert "spec_entities" not in record and "requirement_ids" not in record
    assert "source_type" not in record and "domain" not in record
    assert record["chunk_role_source"] == "rules"
    assert round(sum(record["chunk_role_scores"].values()), 4) == 1.0


def test_unmatched_chunk_reports_no_role_rather_than_inventing_one():
    record = ingest.enrich_spec_record({
        "chunk_id": "axi_spec#0002#000", "chunk_type": "prose",
        "document_id": "axi_spec", "doc_name": "AXI spec.pdf",
        "context_header": "AXI > Mnemonics", "start_page": 3, "end_page": 3,
        "tables_cited": [], "text": "RV32 RV64 Mnemonic cbo.zero base",
    })

    assert record["chunk_role"] is None
    assert record["chunk_role_source"] == "unresolved"
    assert set(record["chunk_role_scores"].values()) == {0.0}


def test_tables_persist_raw_columns_and_rows_but_are_not_hierarchy_nodes(monkeypatch):
    sections = ingest.build_sections(OutlineDocument(), "fpu_spec", "fpu.pdf")
    monkeypatch.setattr(ingest, "build_caption_index", lambda document: {
        "2.1": {"page": 4, "caption": "Interface", "bbox": (0, 0, 1, 1)}})
    monkeypatch.setattr(ingest, "extract_table", lambda page, bbox, next_page=None: (
        [["Signal", "Width", "Description"],
         ["rm_i", "3", "IEEE rounding mode"]], "lines"))

    tables = ingest.build_tables(PageSequence(20), "fpu_spec", "fpu.pdf", sections)
    table = tables["2.1"]

    assert table["table_id"] == "fpu_spec::table::2.1"
    assert table["columns"] == ["Signal", "Width", "Description"]
    assert table["rows"] == [["rm_i", "3", "IEEE rounding mode"]]
    assert table["section_id"] == sections[0]["node_id"]
    assert table["subsection_id"] == sections[1]["node_id"]
    assert "node_id" not in table and "type" not in table


def test_table_chunk_lists_itself_in_table_ids():
    record = ingest.enrich_spec_record({
        "chunk_id": "fpu_spec#table#2.1", "chunk_type": "table",
        "document_id": "fpu_spec", "doc_name": "fpu.pdf",
        "context_header": "fpu.pdf > Table 2.1", "table_label": "2.1",
        "table_id": "fpu_spec::table::2.1", "section_id": "fpu_spec::sec::0000",
        "section_title": "Floating Point Unit", "subsection_id": None,
        "subsection_title": None, "start_page": 4, "end_page": 4,
        "tables_cited": [], "text": "| Signal | Width |\n|---|---|\n| rm_i | 3 |",
    })

    assert record["source_id"] == "fpu_spec::table::2.1"
    assert record["table_ids"] == ["fpu_spec::table::2.1"]
    assert record["page"] == 4


def test_sliding_window_preserves_sentence_page_pairs():
    chunks = ingest.sliding_window_chunks(
        [("first sentence", 19), ("second sentence", 20)], max_chars=100, overlap=0)

    assert chunks == [[("first sentence", 19), ("second sentence", 20)]]


class FakeTable:
    """Minimal pymupdf table stand-in carrying only geometry and cells."""

    def __init__(self, top, bottom, rows):
        """Record the table's vertical span and its extracted cells."""
        self.bbox = (0, top, 500, bottom)
        self._rows = rows
        self.row_count = len(rows)
        self.col_count = max(len(row) for row in rows)

    def extract(self):
        """Return the table's cells the way pymupdf does."""
        return self._rows


class FakePage:
    """Minimal pymupdf page stand-in returning a fixed set of ruled tables."""

    def __init__(self, tables, height=800):
        """Hold the page's ruled tables and its height in points."""
        self._tables = tables
        self.rect = type("Rect", (), {"height": height, "width": 500})()

    def find_tables(self, strategy=None, clip=None):
        """Return the page's ruled tables regardless of strategy."""
        return type("Found", (), {"tables": self._tables})()

    def get_text(self, kind=None):
        """Report no text blocks, so the alignment fallback finds nothing."""
        return []


def test_equally_close_tables_resolve_to_the_one_below_the_caption():
    above = FakeTable(100, 180, [["a", "b"], ["1", "2"]])
    below = FakeTable(210, 300, [["c", "d"], ["3", "4"]])
    page = FakePage([above, below])

    rows, method = ingest.extract_table(page, (0, 190, 500, 200))

    assert method == "lines"
    assert rows == [["c", "d"], ["3", "4"]]


def test_the_closer_table_wins_even_when_it_sits_above_the_caption():
    above = FakeTable(100, 185, [["a", "b"], ["1", "2"]])
    below = FakeTable(240, 300, [["c", "d"], ["3", "4"]])
    page = FakePage([above, below])

    rows, method = ingest.extract_table(page, (0, 190, 500, 200))

    assert method == "lines"
    assert rows == [["a", "b"], ["1", "2"]]


def test_caption_printed_under_its_table_still_binds_to_that_table():
    above = FakeTable(100, 180, [["a", "b"], ["1", "2"]])
    page = FakePage([above])

    rows, method = ingest.extract_table(page, (0, 190, 500, 200))

    assert method == "lines"
    assert rows == [["a", "b"], ["1", "2"]]


def test_caption_ending_a_page_binds_to_the_table_carried_onto_the_next():
    page = FakePage([])
    carried = FakePage([FakeTable(40, 120, [["e", "f"], ["5", "6"]])])

    rows, method = ingest.extract_table(page, (0, 700, 500, 720), next_page=carried)

    assert method == "lines_page_break"
    assert rows == [["e", "f"], ["5", "6"]]


def test_a_table_far_from_the_caption_is_not_treated_as_its_body():
    page = FakePage([FakeTable(600, 700, [["a", "b"], ["1", "2"]])])

    rows, method = ingest.extract_table(page, (0, 100, 500, 120))

    assert (rows, method) == ([], "none")


class TypesetPage:
    """Minimal pymupdf page stand-in returning styled text lines."""

    def __init__(self, lines):
        """Hold ``(text, size, bold)`` triples laid out down the page."""
        self._lines = lines

    def get_text(self, kind=None):
        """Return the page's lines in pymupdf's ``dict`` shape."""
        spans = []
        for index, (text, size, bold) in enumerate(self._lines):
            top = 100 + index * 20
            spans.append({
                "bbox": (0, top, 400, top + 12),
                "spans": [{"text": text, "size": size,
                           "font": "Bold" if bold else "Regular", "flags": 0}],
            })
        return {"blocks": [{"type": 0, "lines": spans}]}


class UnoutlinedDocument:
    """PDF stand-in that ships no outline, forcing typographic detection."""

    def __init__(self, pages):
        """Hold the document's typeset pages."""
        self._pages = pages
        self.page_count = len(pages)

    def __getitem__(self, index):
        """Return one page by zero-based index."""
        return self._pages[index]

    def get_toc(self):
        """Report an absent outline."""
        return []


def test_headings_are_recovered_from_type_when_a_pdf_ships_no_outline():
    body = [("Body text that carries the bulk of the characters on this page.", 10.0, False)]
    doc = UnoutlinedDocument([
        TypesetPage([("1 Overview", 14.0, True)] + body * 6),
        TypesetPage([("1.1. Rounding modes", 10.0, True)] + body * 6),
    ])

    assert ingest.detect_headings(doc) == [
        (1, "1 Overview", 1), (2, "1.1. Rounding modes", 2)]


def test_a_section_number_set_on_its_own_line_rejoins_its_title():
    body = [("Body text that carries the bulk of the characters on this page.", 10.0, False)]
    doc = UnoutlinedDocument([
        TypesetPage([("A1.2.1", 11.4, True), ("Interface and interconnect", 11.4, True)]
                    + body * 6),
    ])

    assert ingest.detect_headings(doc) == [(4, "A1.2.1 Interface and interconnect", 1)]


def test_contents_entries_and_repeated_table_headers_are_not_headings():
    body = [("Body text that carries the bulk of the characters on this page.", 10.0, False)]
    doc = UnoutlinedDocument([
        TypesetPage([("1.5. Base Instruction-Length Encoding.........12", 10.0, True)] + body * 6),
    ] + [TypesetPage([("Extension", 10.0, True)] + body * 6) for _ in range(6)])

    assert ingest.detect_headings(doc) == []


def test_a_present_outline_is_preferred_over_typographic_detection():
    sections = ingest.build_sections(OutlineDocument(), "fpu_spec", "fpu.pdf")

    assert [section["raw_title"] for section in sections] == [
        "1 Floating Point Unit", "1.1 Interface signals", "1.2 Rounding", "2 Exceptions"]


def test_a_short_table_stays_in_one_unsplit_window():
    columns = ["Signal", "Width"]
    rows = [["rm_i", "3"], ["rs_o", "1"]]

    assert ingest.table_row_windows(columns, rows) == [(0, rows)]


def test_a_long_table_splits_into_windows_that_cover_every_row_once():
    columns = ["Signal", "Width", "Description"]
    rows = [[f"signal_{index}", "32", "a description long enough to force windowing"]
            for index in range(40)]

    windows = ingest.table_row_windows(columns, rows, max_chars=300)

    assert len(windows) > 1
    assert [row for _, window in windows for row in window] == rows
    assert [start for start, _ in windows] == [0] + [
        sum(len(window) for _, window in windows[:index])
        for index in range(1, len(windows))]


def test_every_window_of_a_split_table_restates_the_header_row():
    columns = ["Signal", "Width"]
    rows = [[f"sig_{index}", "8"] for index in range(30)]

    windows = ingest.table_row_windows(columns, rows, max_chars=120)
    rendered = [ingest.rows_to_markdown([columns] + window) for _, window in windows]

    assert len(rendered) > 1
    assert all(text.startswith("| Signal | Width |") for text in rendered)


def test_a_single_oversized_row_is_admitted_rather_than_stalling():
    columns = ["Signal", "Description"]
    rows = [["wide_signal", "x" * 500], ["narrow", "y"]]

    windows = ingest.table_row_windows(columns, rows, max_chars=100)

    assert [row for _, window in windows for row in window] == rows
    assert windows[0][1] == [rows[0]]


def test_windowed_table_chunks_stay_under_the_configured_character_cap():
    columns = ["Signal", "Width"]
    rows = [[f"sig_{index}", "8"] for index in range(60)]

    for _, window in ingest.table_row_windows(columns, rows):
        assert len(ingest.rows_to_markdown([columns] + window)) <= cfg.MAX_TABLE_CHUNK_CHARS


def test_caption_titled_by_a_lowercase_register_name_is_a_caption():
    # RISC-V captions open with CSR names, e.g. "Table 44. vtype register layout".
    match = ingest._CAPTION.match("Table 44. vtype register layout")

    assert match and match.group(2, 4) == ("44", "vtype register layout")


def test_prose_opening_with_a_table_reference_is_not_a_caption():
    # Backtracking once split "A2.2" into label "A2" and title "2 shows ...".
    assert not ingest._CAPTION.match("Table A2.2 shows the VALID and READY signals.")


def test_continued_table_banner_is_not_a_caption():
    assert not ingest._CAPTION.match("Table A15.3 \u2013 Continued from previous page")


def test_split_sentences_splits_on_terminal_punctuation_and_rejoins_false_endings():
    text = "Hello world.  Next one!\nThird? See e.g. the table. Bit A. Done.   "

    assert ingest.split_sentences(text) == [
        "Hello world.", "Next one!", "Third?", "See e.g. the table.", "Bit A. Done."]
    assert ingest.split_sentences("   ") == []


def test_ingest_does_not_load_langchain_text_splitters():
    import subprocess
    import sys

    probe = "import ingest, sys; print('langchain_text_splitters' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", probe], cwd=Path(ingest.__file__).parent,
                            capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "False"
