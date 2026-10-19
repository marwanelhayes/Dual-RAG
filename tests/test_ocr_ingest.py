"""Markdown ingestion keeps source pages, hierarchy and structured table evidence."""

from pathlib import Path

import pytest

import configure as cfg
import ocr_ingest as ocr


@pytest.fixture(autouse=True)
def token_counter(monkeypatch):
    import ingest
    monkeypatch.setattr(ingest, "count_tokens", lambda text: len(text.split()))


def test_markdown_sections_tables_and_page_provenance():
    pages = [
        "Revision 2.0\n\n# 1 Interface\n\nSee Table 1.1.\n\n"
        "## 1.1 Signals\n\nTable 1.1: Interface signals\n\n"
        "| Signal | Width | Description |\n|---|---|---|\n| READY | 1 | Ready flag |",
        "Table 1.1 (continued)\n\n| Signal | Width | Description |\n"
        "|---|---|---|\n| VALID | 1 | Valid flag |\n\nThe sender must wait.\n\n"
        "![waveform](images/figure.png)\n\n# 2 Timing\n\nA new section.",
    ]
    nodes, tables, records = ocr.ingest_markdown(pages, Path("spec.pdf"))
    assert [node["section_number"] for node in nodes] == [None, "1", "1.1", "2"]
    assert nodes[2]["parent_id"] == nodes[1]["node_id"]
    table = tables["1.1"]
    assert table["rows"] == [["READY", "1", "Ready flag"], ["VALID", "1", "Valid flag"]]
    assert table["hierarchy_id"] == nodes[2]["node_id"]
    prose = [record for record in records if record["chunk_type"] == "prose"]
    assert all("Ready flag" not in record["text"] for record in prose)
    assert all("figure.png" not in record["text"] for record in records)
    assert next(record for record in prose if "sender" in record["text"])["page"] == 2
    assert next(record for record in prose if "See Table" in record["text"])["table_ids"] == [
        "spec::table::1.1"]
    assert all(record["revision"] == "Revision 2.0" for record in records)
    assert next(record for record in records if record["chunk_type"] == "table")["end_page"] == 2


def test_qianfan_html_tables_expand_merged_cells_and_never_enter_prose():
    markdown = """# 1 Registers

<table><tr><th>Name</th><th>Description</th></tr>
<tr><td rowspan="2">STATUS</td><td>Ready &amp; valid</td></tr>
<tr><td>Busy<br>flag</td></tr><tr><td colspan="2">Reserved</td></tr></table>

Table 2: Register map

The register shall reset.
"""
    _, tables, records = ocr.ingest_markdown([markdown], Path("spec.pdf"))
    assert tables["2"]["columns"] == ["Name", "Description"]
    assert tables["2"]["rows"] == [
        ["STATUS", "Ready & valid"], ["STATUS", "Busy flag"], ["Reserved", "Reserved"]]
    prose = " ".join(record["text"] for record in records if record["chunk_type"] == "prose")
    assert "STATUS" not in prose and "Table 2:" not in prose


def test_fenced_code_is_not_a_heading_or_table_and_unnumbered_headings_work():
    nodes, tables, records = ocr.ingest_markdown([
        "# Overview\n\n```verilog\n# not a heading\n| not | a table |\n```\n\n"
        "### Details\n\nBody text.\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    ], Path("spec.pdf"))
    assert [node["section_name"] for node in nodes] == ["Overview", "Details"]
    assert nodes[1]["parent_id"] == nodes[0]["node_id"]
    assert len(tables) == 1
    assert tables["unlabelled-1"]["rows"] == [["1", "2"]]
    assert any("# not a heading" in record["text"] for record in records)


def test_headerless_pages_and_setext_headers_are_not_dropped():
    nodes, _, records = ocr.ingest_markdown([
        "Front matter text.", "Overview\n========\n\nThe body must survive."
    ], Path("spec.pdf"))
    assert len(nodes) == 2
    assert any(record["page"] == 1 and "Front matter" in record["text"] for record in records)


def test_oversized_ocr_chunks_still_fail(monkeypatch):
    monkeypatch.setattr(cfg, "ENCODER_MAX_TOKENS", 8)
    with pytest.raises(ValueError, match="encoder limit"):
        ocr.ingest_markdown(["# 1 Test\n\n" + "word " * 20], Path("spec.pdf"))


def test_table_reference_is_prose_and_unbannered_continuations_keep_the_label():
    _, tables, records = ocr.ingest_markdown([
        "# 1 Registers\n\nTable 2 describes the registers.\n\n"
        "Table 2: Registers\n\n| Name | Width |\n|---|---|\n| A | 1 |",
        "| Name | Width |\n|---|---|\n| B | 2 |",
    ], Path("spec.pdf"))
    assert list(tables) == ["2"]
    assert tables["2"]["rows"] == [["A", "1"], ["B", "2"]]
    assert any(record["text"] == "Table 2 describes the registers."
               and record["chunk_type"] == "prose" for record in records)


def test_ocr_cache_is_per_source_and_model_configuration(tmp_path):
    import fitz

    class Engine:
        identity = {"model": "fake", "revision": "v1", "dpi": 144}
        calls = 0

        def recognize(self, image):
            self.calls += 1
            return "# 1 Test\n\nText."

    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.new_page()
        document.save(source)
    engine = Engine()
    cache = tmp_path / "cache"
    assert len(ocr.ocr_pages(source, engine, cache)) == 2
    assert engine.calls == 2
    ocr.ocr_pages(source, engine, cache)
    assert engine.calls == 2
    engine.identity = {**engine.identity, "revision": "v2"}
    ocr.ocr_pages(source, engine, cache)
    assert engine.calls == 4


def test_html_table_caption_and_prose_survive_without_blank_lines():
    _, tables, records = ocr.ingest_markdown([
        "# 1 Test\n\n<table><tr><td>Name</td><td>Width</td></tr>"
        "<tr><td>VALID</td><td>1</td></tr></table>\n"
        "<p>Table 1: Signals</p>\n<p>The sender must wait.</p><img src='figure.png'>"
    ], Path("spec.pdf"))
    assert tables["1"]["rows"] == [["VALID", "1"]]
    assert any("sender must wait" in record["text"]
               for record in records if record["chunk_type"] == "prose")
