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

    def get_toc(self, simple=True):
        """Return a deterministic two-level outline, with empty destinations if asked."""
        toc = [
            (1, "1 Floating Point Unit", 2),
            (2, "1.1 Interface signals", 3),
            (2, "1.2 Rounding", 5),
            (1, "2 Exceptions", 8),
        ]
        return toc if simple else [(*entry, {}) for entry in toc]


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
        "table_ids": ["axi_spec::table::A3.2"],
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
    assert not {"chunk_role", "chunk_role_scores", "chunk_role_source"} & set(record)


def test_tables_persist_raw_columns_and_rows_but_are_not_hierarchy_nodes(monkeypatch):
    sections = ingest.build_sections(OutlineDocument(), "fpu_spec", "fpu.pdf")
    monkeypatch.setattr(ingest, "build_caption_index", lambda document: {
        "2.1": {"page": 4, "caption": "Interface", "bbox": (0, 0, 1, 1)}})
    monkeypatch.setattr(ingest, "extract_table", lambda page, bbox, next_page=None: (
        [["Signal", "Width", "Description"],
         ["rm_i", "3", "IEEE rounding mode"]], "lines", (0, (0, 1, 1, 2))))

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
        "text": "| Signal | Width |\n|---|---|\n| rm_i | 3 |",
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

    rows, method, _ = ingest.extract_table(page, (0, 190, 500, 200))

    assert method == "lines"
    assert rows == [["c", "d"], ["3", "4"]]


def test_the_closer_table_wins_even_when_it_sits_above_the_caption():
    above = FakeTable(100, 185, [["a", "b"], ["1", "2"]])
    below = FakeTable(240, 300, [["c", "d"], ["3", "4"]])
    page = FakePage([above, below])

    rows, method, _ = ingest.extract_table(page, (0, 190, 500, 200))

    assert method == "lines"
    assert rows == [["a", "b"], ["1", "2"]]


def test_caption_printed_under_its_table_still_binds_to_that_table():
    above = FakeTable(100, 180, [["a", "b"], ["1", "2"]])
    page = FakePage([above])

    rows, method, _ = ingest.extract_table(page, (0, 190, 500, 200))

    assert method == "lines"
    assert rows == [["a", "b"], ["1", "2"]]


def test_caption_ending_a_page_binds_to_the_table_carried_onto_the_next():
    page = FakePage([])
    carried = FakePage([FakeTable(40, 120, [["e", "f"], ["5", "6"]])])

    rows, method, _ = ingest.extract_table(page, (0, 700, 500, 720), next_page=carried)

    assert method == "lines_page_break"
    assert rows == [["e", "f"], ["5", "6"]]


def test_a_table_far_from_the_caption_is_not_treated_as_its_body():
    page = FakePage([FakeTable(600, 700, [["a", "b"], ["1", "2"]])])

    rows, method, _ = ingest.extract_table(page, (0, 100, 500, 120))

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

    def get_toc(self, simple=True):
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


DEAD_CHUNK_FIELDS = {
    "document_role", "sentence_sources", "prev_section", "next_section", "parent_name",
    "n_sentences", "parent_number", "level", "own_start_page", "own_end_page",
    "section_start_page", "section_end_page", "n_chars",
    "chunk_role", "chunk_role_scores", "chunk_role_source", "tables_cited"}


def _tiny_pdf(tmp_path):
    """Write a two-page PDF with a two-level outline and return its path."""
    import fitz

    path = tmp_path / "tiny.pdf"
    doc = fitz.open()
    for page_number in range(2):
        doc.new_page().insert_text(
            (72, 120), f"AWVALID is asserted by the manager until AWREADY. Page "
            f"{page_number} carries enough words to pass the minimum section size.",
            fontsize=10)
    doc.set_toc([[1, "1 Channels", 1], [2, "1.1 Write address", 2]])
    doc.save(path)
    return path


def test_chunks_and_hierarchy_carry_no_unread_fields(tmp_path):
    """Fields nothing downstream reads are not emitted."""
    nodes, _, records = ingest.ingest_pdf(_tiny_pdf(tmp_path))

    assert records and not DEAD_CHUNK_FIELDS & set().union(*records)
    assert not {"parent_number", "parent_name", "prev_section", "next_section"} & set(
        ).union(*nodes)


def test_table_references_resolve_to_table_ids_once_and_drop_unknown_labels():
    tables = {"A3.2": {"table_id": "axi::table::A3.2", "caption": "c", "page": 1}}

    assert ingest.find_table_refs(
        "See Table A3.2, Table Z9, and again Table A3.2.", tables) == ["axi::table::A3.2"]


def test_context_header_is_document_and_section_path_without_revision(tmp_path):
    """The revision is metadata; embedding it adds the same noise to every chunk."""
    _, _, records = ingest.ingest_pdf(_tiny_pdf(tmp_path))

    assert [record["context_header"] for record in records] == [
        "tiny.pdf > 1 Channels", "tiny.pdf > 1 Channels > 1.1 Write address"]


def _running_header_pdf(tmp_path):
    """Write a five-page PDF whose header text changes per page; page 1 has none."""
    import fitz

    path = tmp_path / "headers.pdf"
    doc = fitz.open()
    for index in range(5):
        page = doc.new_page()
        if index:
            page.insert_text((72, 30), f"Chapter A{index} Topic {index}", fontsize=9)
        page.insert_text((72, 300), f"Body sentence {index} says AWVALID stays high until "
                         "AWREADY is sampled on a rising clock edge.", fontsize=10)
        page.insert_text((72, 800), f"Page {index + 1} of 5", fontsize=9)
    doc.set_toc([[1, "1 Channels", 1]])
    doc.save(path)
    return path


def test_running_headers_and_footers_are_stripped_by_position_not_text(tmp_path):
    """Per-page header text never repeats, so only its position identifies it."""
    _, _, records = ingest.ingest_pdf(_running_header_pdf(tmp_path))
    text = " ".join(record["text"] for record in records)

    assert "Chapter A" not in text and "of 5" not in text
    assert all(f"Body sentence {index}" in text for index in range(5))


def test_furniture_counts_report_band_and_text_only_strips(tmp_path):
    import fitz

    with fitz.open(_running_header_pdf(tmp_path)) as doc:
        counts = ingest.furniture_counts(doc)

    assert counts == {"furniture_blocks_stripped": 9, "furniture_text_only_hits": 0}


def test_hierarchy_document_records_furniture_counts():
    sections = ingest.build_sections(OutlineDocument(), "fpu_spec", "fpu.pdf")
    counts = {"furniture_blocks_stripped": 9, "furniture_text_only_hits": 0}

    document = ingest.hierarchy_document(
        Path("fpu.pdf"), "Revision A", OutlineDocument.page_count, sections, counts)

    assert document["furniture_blocks_stripped"] == 9
    assert document["furniture_text_only_hits"] == 0


def test_fallback_heading_scores_match_outline_by_title_and_page():
    toc = [(1, "1 Intro", 1), (2, "1.1 Scope", 2)]
    detected = [(1, "1  intro", 1), (1, "Signal Width", 3)]

    assert ingest.fallback_heading_scores(detected, toc) == {
        "fallback_heading_precision": 0.5, "fallback_heading_recall": 0.5,
        "fallback_heading_f1": 0.5}
    assert ingest.fallback_heading_scores([], toc)["fallback_heading_f1"] == 0.0


def _points_pdf(tmp_path):
    """Write a one-page PDF whose two outline entries point at their printed headings."""
    import fitz

    path = tmp_path / "points.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "1 Alpha", fontsize=12)
    page.insert_text((72, 130), "Alpha body says AWVALID stays high until AWREADY.", fontsize=10)
    page.insert_text((72, 400), "2. Beta", fontsize=12)
    page.insert_text((72, 430), "Beta is short.", fontsize=10)
    doc.set_toc([
        [1, "1 Alpha", 1, {"kind": fitz.LINK_GOTO, "to": fitz.Point(0, 85)}],
        [1, "2 Beta Section", 1, {"kind": fitz.LINK_GOTO, "to": fitz.Point(0, 385)}]])
    doc.save(path)
    return path


def test_sections_are_cut_at_outline_points_not_by_title_text(tmp_path):
    """The printed heading differs from the outline title; the short section survives."""
    _, _, records = ingest.ingest_pdf(_points_pdf(tmp_path))
    by_section = {record["section_name"]: record["text"] for record in records}

    assert "Alpha body" in by_section["Alpha"] and "Beta" not in by_section["Alpha"]
    assert "Beta is short." in by_section["Beta Section"]


def test_printed_heading_block_stays_out_of_section_text(tmp_path):
    """The breadcrumb already names the section; a block that differs from the title stays."""
    _, _, records = ingest.ingest_pdf(_points_pdf(tmp_path))
    by_section = {record["section_name"]: record["text"] for record in records}

    assert by_section["Alpha"].startswith("Alpha body")
    assert by_section["Beta Section"] == "2. Beta Beta is short."


def test_heading_block_matches_title_across_wrapped_lines():
    assert ingest.is_heading_block("Chapter 24. BFloat16-precision Floating-\nPoint, V1\n",
                                   "Chapter 24. BFloat16-precision Floating-Point, V1")
    assert not ingest.is_heading_block("Alpha body says AWVALID.\n", "1 Alpha")


def _ruled_table_pdf(tmp_path):
    """Write one page: prose, a captioned ruled 2x3 table, then more prose."""
    import fitz

    path = tmp_path / "ruled.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "The write channel carries the address. It is described below.",
                     fontsize=10)
    page.insert_text((72, 140), "Table 1.1: Channel signals", fontsize=10)
    left, top, width, height = 72, 160, 300, 20
    for row in range(4):
        page.draw_line((left, top + row * height), (left + width, top + row * height))
    for column in range(3):
        page.draw_line((left + column * width / 2, top), (left + column * width / 2, top + 60))
    for row, cells in enumerate([("Signal", "Source"), ("CELLAWID", "Manager"),
                                 ("CELLAWLEN", "Manager")]):
        for column, text in enumerate(cells):
            page.insert_text((left + 4 + column * width / 2, top + 14 + row * height), text,
                             fontsize=9)
    page.insert_text((72, 260), "After the table the prose continues with more words.",
                     fontsize=10)
    doc.set_toc([[1, "1 Channels", 1]])
    doc.save(path)
    return path


def test_table_cells_never_enter_prose(tmp_path):
    _, _, records = ingest.ingest_pdf(_ruled_table_pdf(tmp_path))
    prose = " ".join(record["text"] for record in records if record["chunk_type"] == "prose")
    table = " ".join(record["text"] for record in records if record["chunk_type"] == "table")

    assert "CELLAWID" in table
    assert "CELLAWID" not in prose and "Signal Source" not in prose
    assert "prose continues" in prose


def _node(node_id, page, y, path):
    return {"node_id": node_id, "own_start_page": page, "start_y": y, "path": path,
            "section_id": node_id, "section_title": path[-1], "subsection_id": None,
            "subsection_title": None, "section_number": None}


def test_a_table_belongs_to_the_section_whose_heading_sits_above_its_caption(monkeypatch):
    nodes = [_node("s1", 4, 100.0, ["1 Bus"]), _node("s2", 4, 500.0, ["1 Bus", "1.2 Later"])]
    monkeypatch.setattr(ingest, "build_caption_index", lambda document: {
        "1.1": {"page": 4, "caption": "Signals", "bbox": (0, 300, 1, 310)}})
    monkeypatch.setattr(ingest, "extract_table", lambda page, bbox, next_page=None: (
        [["Signal", "Width"], ["rm_i", "3"]], "lines", None))

    table = ingest.build_tables(PageSequence(20), "spec", "spec.pdf", nodes)["1.1"]

    assert table["hierarchy_id"] == "s1"


def test_a_table_chunk_header_names_its_section_path(tmp_path):
    _, _, records = ingest.ingest_pdf(_ruled_table_pdf(tmp_path))

    assert [record["context_header"] for record in records
            if record["chunk_type"] == "table"] == [
        "ruled.pdf > 1 Channels > Table 1.1: Channel signals"]
