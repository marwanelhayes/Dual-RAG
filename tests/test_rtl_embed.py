"""Focused tests for module-first RTL retrieval records."""

import json
from pathlib import Path


import configure as cfg
import rtl_embed as re_
from embed import build_manifest, read_manifest
from ingest import content_hash


def _declaration(module, name, kind="SIGNAL", direction="internal", **metadata):
    prefix = {"PORT": "port", "SIGNAL": "sig", "PARAMETER": "param"}[kind]
    signal_id = f"rtl::{prefix}::{module}::{name}"
    return {
        "signal_id": signal_id,
        "construct_type": kind,
        "original_name": name,
        "normalized_name": name.lower(),
        "normalized_tokens": [name.lower()],
        "direction": direction,
        "data_type": "logic",
        "width": 1,
        "file_path": f"{module}.sv",
        "start_line": 2,
        "end_line": 2,
        "reader_sites": [],
        "writer_sites": [],
        "content_hash": content_hash(kind, module, name),
        **metadata,
    }


def _rtl(source):
    signal = _declaration(
        "fpu_decoder", "FPUValidD",
        writer_sites=[{"module": "fpu_decoder", "file": str(source), "line": 12}],
    )
    port = _declaration("fpu_decoder", "enable", "PORT", "input")
    always = {
        "block_id": "rtl::blk::fpu_decoder::always::10:3-14",
        "kind": "always",
        "construct_type": "ALWAYS_FF",
        "original_name": "always@10",
        "file": str(source),
        "start_line": 10,
        "end_line": 14,
        "source_code": "always_ff @(posedge clk) FPUValidD <= enable;",
        "signals_read": ["clk", "enable"],
        "signals_written": ["FPUValidD"],
        "sensitivity": [{"signal": "clk", "edge": "posedge"}],
        "clock": "clk",
        "reset": None,
        "content_hash": content_hash("ALWAYS_FF", "fpu_decoder", "10-14"),
    }
    function = {
        "block_id": "rtl::blk::fpu_decoder::func::16:3-18",
        "kind": "func",
        "construct_type": "FUNCTION",
        "original_name": "decodeRoundMode",
        "file": str(source),
        "start_line": 16,
        "end_line": 18,
        "source_code": "function decodeRoundMode; endfunction",
        "signals_read": ["enable"],
        "signals_written": [],
        "content_hash": content_hash("FUNCTION", "fpu_decoder", "16-18"),
    }
    group = {
        "block_id": "rtl::assign_group::fpu_decoder::20-21",
        "kind": "assignment_group",
        "construct_type": "ASSIGNMENT_GROUP",
        "original_name": "assignments@20",
        "file": str(source),
        "start_line": 20,
        "end_line": 21,
        "source_code": "assign a = b;\nassign c = d;",
        "signals_read": ["b", "d"],
        "signals_written": ["a", "c"],
        "assignments": [{}, {}],
        "content_hash": content_hash("ASSIGNMENT_GROUP", "fpu_decoder", "20-21"),
    }
    instance = {
        "instance_id": "rtl::instdef::fpu_decoder::u_child::23:3",
        "original_name": "u_child",
        "target_module": "child",
        "bindings": [{"formal": "din", "actual_refs": ["FPUValidD"]}],
    }
    module = {
        "module_id": "rtl::mod::fpu_decoder",
        "original_name": "fpu_decoder",
        "normalized_name": "floating point unit decoder",
        "normalized_tokens": ["floating", "point", "unit", "decoder"],
        "file": str(source),
        "start_line": 1,
        "end_line": 24,
        "ports": [port],
        "signals": [signal],
        "parameters": [],
        "instances": [instance],
        "functions_tasks": [function],
        "always_blocks": [always],
        "assignment_groups": [group],
        "other_blocks": [{"construct_type": "INITIAL", "start_line": 9}],
        "source_code": ("module fpu_decoder; // rounding mode and valid flag\n"
                        "endmodule\n"),
        "content_hash": content_hash("MODULE", "fpu_decoder"),
    }
    child = {
        "module_id": "rtl::mod::child",
        "original_name": "child",
        "normalized_name": "child",
        "normalized_tokens": ["child"],
        "file": str(source),
        "start_line": 25,
        "end_line": 26,
        "ports": [], "signals": [], "parameters": [], "instances": [],
        "functions_tasks": [], "always_blocks": [], "assignment_groups": [],
        "other_blocks": [], "source_code": "module child; endmodule\n",
        "content_hash": content_hash("MODULE", "child"),
    }
    return {
        "schema_version": 2,
        "top_module": "fpu_decoder",
        "tree": [
            {"depth": 0, "instance": "top", "module": "fpu_decoder"},
            {"depth": 1, "instance": "u_child", "module": "child"},
        ],
        "modules": {"fpu_decoder": module, "child": child},
        "warnings": [],
    }


def test_comments_keep_prose_and_drop_decorative_rules():
    text = "// useful explanation\n// -----------\nlogic x; // another note\n"
    assert re_.comments(text) == ["useful explanation", "another note"]


def test_children_are_read_from_the_authoritative_instance_tree():
    tree = [
        {"depth": 0, "module": "top"},
        {"depth": 1, "module": "left"},
        {"depth": 2, "module": "leaf"},
        {"depth": 1, "module": "right"},
    ]
    assert re_.children(tree) == {"top": ["left", "right"], "left": ["leaf"]}


def test_records_are_module_first_and_do_not_vectorize_signals_or_instances(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    assert [record["source_type"] for record in records] == [
        "module", "module", "construct", "construct", "construct"
    ]
    assert records[0]["chunk_id"] == "rtl:child"
    assert records[1]["chunk_id"] == "rtl:fpu_decoder"
    assert not any(record["source_type"] in {"symbol", "instance"} for record in records)
    assert not any(record.get("construct_type") == "INITIAL" for record in records)
    assert len({record["chunk_id"] for record in records}) == len(records)


def test_module_record_preserves_id_source_provenance_children_and_signal_metadata(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    record = next(record for record in re_.retrieval_records(_rtl(source))
                  if record["module"] == "fpu_decoder" and record["source_type"] == "module")

    assert record["chunk_id"] == "rtl:fpu_decoder"
    assert record["record_id"] == "rtl::mod::fpu_decoder"
    assert record["source_id"].endswith("fpu_decoder.sv")
    assert record["file"] == str(source) and record["line"] == 1
    assert "instantiates: child" in record["text"]
    assert "rounding mode and valid flag" in record["text"]
    assert {signal["original_name"] for signal in record["signal_metadata"]} == {
        "enable", "FPUValidD"
    }


def test_structural_ids_and_surfaces_are_stable_and_bounded(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    structures = [record for record in records if record["source_type"] == "construct"]

    assert structures[0]["chunk_id"] == "rtl:construct:fpu_decoder:always:10-14"
    assert structures[0]["record_id"] == "rtl::blk::fpu_decoder::always::10:3-14"
    assert structures[0]["file"] == str(source) and structures[0]["line"] == 10
    assert structures[0]["signals_written"] == ["FPUValidD"]
    assert structures[-1]["record_id"] == "rtl::assign_group::fpu_decoder::20-21"
    assert all(len(record["source_code"]) <= re_.STRUCTURAL_SURFACE_CHARS
               for record in structures)
    assert [record["chunk_id"] for record in records] == [
        record["chunk_id"] for record in re_.retrieval_records(_rtl(source))
    ]


def test_module_text_names_the_peer_modules_its_ports_are_wired_to(tmp_path):
    source = tmp_path / "hazard_unit.sv"
    source.write_text("module hazard_unit; endmodule\n")
    rtl = {"modules": {}, "instance_tree": []}
    for module in ("hazard_unit", "fetch_stage", "riscv_processor"):
        rtl["modules"][module] = {
            "file": str(source), "start_line": 1, "end_line": 2, "ports": [],
            "signals": [], "parameters": [], "always_blocks": [],
            "functions_tasks": [], "assignment_groups": [], "instances": [],
        }
    rtl["modules"]["riscv_processor"]["instances"] = [
        {"target_module": target, "bindings": [
            {"parent_module": "riscv_processor", "formal_name": "StallD",
             "actual_signals": ["StallD"], "direction": direction}]}
        for target, direction in (("hazard_unit", "output"), ("fetch_stage", "input"))
    ]
    records = re_.retrieval_records(rtl)
    text = {record["chunk_id"]: record["text"] for record in records}

    assert "drives: StallD -> fetch_stage" in text["rtl:hazard_unit"]
    assert "reads: StallD <- hazard_unit" in text["rtl:fetch_stage"]


def test_main_writes_manifest_last_and_skips_an_unchanged_index(monkeypatch, tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    output = tmp_path / "output"
    output.mkdir()
    rtl_path = output / "rtl.json"
    rtl_path.write_text(json.dumps(_rtl(source)))
    chunks_path = output / "rtl_chunks.jsonl"
    index_dir = output / "rtl_index"
    manifest_path = output / "manifest.json"
    builds = []

    class FakeStore:
        def save(self, path):
            Path(path).mkdir(parents=True)

        def search(self, query, k):
            return []

    def fake_build(records):
        builds.append(list(records))
        return FakeStore()

    monkeypatch.setattr(cfg, "ROOT", tmp_path)
    monkeypatch.setattr(cfg, "OUTPUT_DIR", output)
    monkeypatch.setattr(cfg, "RTL_PATH", rtl_path)
    monkeypatch.setattr(cfg, "RTL_CHUNKS_PATH", chunks_path)
    monkeypatch.setattr(cfg, "RTL_INDEX_DIR", index_dir)
    monkeypatch.setattr(cfg, "RTL_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(re_.VectorStore, "build", staticmethod(fake_build))
    monkeypatch.setattr(re_.sys, "argv", ["rtl_embed.py"])

    re_.main()
    re_.main()

    assert len(builds) == 1
    assert read_manifest(manifest_path) == build_manifest(builds[0])


def test_module_facets_are_bounded_into_one_chunk():
    parts = [("identity", "module wide"), ("ports", "ports: " + "a, " * 200),
             ("comments", "comments: " + "b " * 200)]

    kept = re_._budgeted_facets(parts, max_chars=400)

    assert kept[0] == "module wide"
    assert len("\n".join(kept)) <= 400


def test_a_small_module_keeps_every_facet():
    parts = [("identity", "module tiny"), ("ports", "ports: clk, rst")]

    assert re_._budgeted_facets(parts, max_chars=400) == [
        "module tiny", "ports: clk, rst"]


def test_a_long_trailing_facet_is_trimmed_rather_than_dropped():
    parts = [("identity", "module m"), ("comments", "comments: " + "x" * 500)]

    kept = re_._budgeted_facets(parts, max_chars=200)

    assert len(kept) == 2
    assert kept[1].startswith("comments: ")
    assert len("\n".join(kept)) <= 200


def test_source_is_trimmed_on_whole_lines_only():
    source = "\n".join(f"line {index}" for index in range(40))

    trimmed = re_._whole_lines(source, max_chars=60)

    assert len(trimmed) <= 60
    assert source.startswith(trimmed)
    assert not trimmed.endswith("lin")
    assert all(line in source.split("\n") for line in trimmed.split("\n"))


def test_a_single_long_source_line_is_kept_rather_than_dropped():
    trimmed = re_._whole_lines("x" * 500 + "\nshort", max_chars=100)

    assert trimmed == "x" * 100


def test_decoration_banners_are_stripped_from_comments():
    source = "// ─── risc_alu: multiplication unit ────────────\nassign a = b;"

    assert re_.comments(source) == ["risc_alu: multiplication unit"]
