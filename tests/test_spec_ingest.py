"""Hermetic tests for hierarchy-only specification ingestion."""

from pathlib import Path

import ingest


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
    monkeypatch.setattr(ingest, "extract_table", lambda page, bbox: (
        [["Signal", "Width", "Description"],
         ["rm_i", "3", "IEEE rounding mode"]], "lines"))

    tables = ingest.build_tables([object()] * 20, "fpu_spec", "fpu.pdf", sections)
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
