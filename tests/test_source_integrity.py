"""Search summaries cannot authorize quotes attributed to original RTL source."""

import agent
from answer_contract import validate_answer
from embed import record_to_document
import rtl_embed


def test_generated_terms_cannot_validate_as_source_quotes():
    source = "module alu;\n  assign y = a + b;\nendmodule\n"
    record = rtl_embed._record(
        "rtl:alu", "rtl::mod::alu", "RTL > alu", "", "module", "alu",
        {"file": "units/alu.sv", "source_code": source, "start_line": 10},
        ["alu"], line=10, start_line=10, end_line=12)
    record = rtl_embed._windowed(record, ["terms: arithmetic logic unit"])[0]
    hit = agent._hit_record(record_to_document(record), "rtl")
    catalog = agent.citable_evidence({"rtl_evidence": [hit]})
    citation = next(iter(catalog))
    assert citation == "[rtl:units/alu.sv:10]"
    assert hit["text"] == source
    assert validate_answer({"status": "evidence_found", "claims": [
        {"citation": citation, "quote": "terms: arithmetic logic unit"}]}, catalog, {"rtl"})[1]
    assert validate_answer({"status": "evidence_found", "claims": [
        {"citation": citation, "quote": "assign y = a + b;"}]}, catalog, {"rtl"})[1] == []


def test_source_windows_preserve_complete_lines_and_physical_locations(monkeypatch):
    monkeypatch.setattr(rtl_embed.cfg, "RTL_CHUNK_CHARS", 20)
    source = "first line\nsecond line\nthird line\n"
    windows = rtl_embed.source_windows(source, 40)
    assert "".join(item["text"] for item in windows) == source
    assert [(item["start_line"], item["end_line"]) for item in windows] == [
        (40, 40), (41, 41), (42, 42)]
