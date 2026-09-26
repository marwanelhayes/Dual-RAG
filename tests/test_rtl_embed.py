"""Focused tests for module-first RTL retrieval records."""

import json
from pathlib import Path

import configure as cfg
import rtl_embed as re_
from embed import build_manifest, read_manifest


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
        **metadata,
    }


def _module(name, source, **fields):
    """Return a schema-4 module entry with empty declarations and blocks."""
    return {
        "module_id": f"rtl::mod::{name}", "original_name": name, "normalized_name": name,
        "normalized_tokens": [name], "file": str(source), "start_line": 1, "end_line": 2,
        "source_code": f"module {name}; endmodule\n", "preamble": "", "ports": [],
        "signals": [], "parameters": [], "instances": [], "always_blocks": [], "functions_tasks": [],
        "assignment_groups": [], "other_blocks": [], **fields,
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
    }
    instance = {
        "instance_id": "rtl::instdef::fpu_decoder::u_child::23:3",
        "original_name": "u_child",
        "target_module": "child",
        "bindings": [{"parent_module": "fpu_decoder", "formal_name": "din",
                      "actual_signals": ["FPUValidD"], "direction": "input"}],
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
        "preamble": "",
        "source_code": ("module fpu_decoder; // rounding mode and valid flag\n"
                        "endmodule\n"),
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
        "other_blocks": [], "source_code": "module child; endmodule\n", "preamble": "",
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


def test_a_module_names_every_module_that_instantiates_it(tmp_path):
    source = tmp_path / "top.sv"
    rtl = {"modules": {
        name: _module(name, source, instances=[
            {"target_module": "mux", "bindings": []}] if name != "mux" else [])
        for name in ("execute_stage", "wb_stage", "mux")}}

    text = {record["chunk_id"]: record["text"] for record in re_.retrieval_records(rtl)}

    assert "instantiated by: execute_stage, wb_stage" in text["rtl:mux"]
    assert "instantiates: mux" in text["rtl:wb_stage"]


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


def test_module_record_preserves_id_provenance_children_and_signal_names(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    record = next(record for record in re_.retrieval_records(_rtl(source))
                  if record["module"] == "fpu_decoder" and record["source_type"] == "module")

    assert record["chunk_id"] == "rtl:fpu_decoder"
    assert record["record_id"] == "rtl::mod::fpu_decoder"
    assert record["file"] == str(source) and record["line"] == 1
    assert "instantiates: child" in record["text"]
    assert "rounding mode and valid flag" in record["text"]
    assert set(record["signal_names"]) == {"enable", "FPUValidD"}
    assert record["lookup_terms"] == ["fpu_decoder"]


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
    assert [record["chunk_id"] for record in records] == [
        record["chunk_id"] for record in re_.retrieval_records(_rtl(source))
    ]


def test_module_text_names_the_peer_modules_its_ports_are_wired_to(tmp_path):
    source = tmp_path / "hazard_unit.sv"
    source.write_text("module hazard_unit; endmodule\n")
    rtl = {"modules": {}, "instance_tree": []}
    for module in ("hazard_unit", "fetch_stage", "riscv_processor"):
        rtl["modules"][module] = _module(module, source)
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


def test_two_readers_of_one_net_do_not_read_from_each_other(tmp_path):
    source = tmp_path / "decode_stage.sv"
    rtl = {"modules": {name: _module(name, source)
                       for name in ("decode_stage", "control_unit", "reg_file")}}
    rtl["modules"]["decode_stage"]["instances"] = [
        {"target_module": target, "bindings": [
            {"parent_module": "decode_stage", "formal_name": "Rs2",
             "actual_signals": ["Rs2D"], "direction": "input"}]}
        for target in ("control_unit", "reg_file")
    ]

    text = {record["chunk_id"]: record["text"] for record in re_.retrieval_records(rtl)}

    assert "reads:" not in text["rtl:control_unit"]
    assert "reads:" not in text["rtl:reg_file"]

def test_main_embeds_on_every_run_and_writes_the_manifest(monkeypatch, tmp_path):
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
            Path(path).mkdir(parents=True, exist_ok=True)

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

    re_.main()
    re_.main()

    assert len(builds) == 2
    assert read_manifest(manifest_path) == build_manifest(builds[0])


def test_a_line_longer_than_a_window_is_split_rather_than_dropped():
    assert re_._windows(["x" * 250, "short"], max_chars=100) == [
        "x" * 100, "x" * 100, "x" * 50 + "\nshort"]


def test_decoration_banners_are_stripped_from_comments():
    source = "// ─── risc_alu: multiplication unit ────────────\nassign a = b;"

    assert re_.comments(source) == ["risc_alu: multiplication unit"]


def test_records_carry_only_fields_a_reader_uses(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    shared = {"chunk_id", "record_id", "source_type", "context_header", "text", "module",
              "file", "line", "start_line", "end_line", "lookup_terms"}

    module = next(record for record in records if record["source_type"] == "module")
    block = next(record for record in records if record["source_type"] == "construct")

    assert set(module) == shared | {"normalized_name", "signal_names"}
    assert set(block) == shared | {"signals_read", "signals_written"}


def test_record_text_does_not_repeat_its_header_or_cite_line_numbers(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    records = re_.retrieval_records(_rtl(source))
    module = next(record for record in records if record["chunk_id"] == "rtl:fpu_decoder")
    block = next(record for record in records if record["source_type"] == "construct")

    assert "structure:" not in module["text"]
    assert "normalized module:" not in module["text"]
    assert block["text"].startswith("reads: clk, enable\n")
    assert "source:" not in block["text"]
    assert "in module fpu_decoder" not in block["text"]


def test_block_comments_are_prose_too():
    source = "/* Instruction encoding reference:\n * FADD.S adds\n */\nassign a = b; // done"

    assert re_.comments(source) == ["Instruction encoding reference:", "FADD.S adds", "done"]


def test_the_header_above_a_module_leads_its_comments(tmp_path):
    source = tmp_path / "hazard_unit.sv"
    rtl = {"modules": {"hazard_unit": _module(
        "hazard_unit", source, preamble="// Generate pipeline stall signals\n",
        source_code="module hazard_unit; // forwarding selects\nendmodule\n")}}

    text = re_.retrieval_records(rtl)[0]["text"]

    assert "comments: Generate pipeline stall signals; forwarding selects" in text


def test_a_long_block_is_embedded_whole_across_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RTL_CHUNK_CHARS", 120)
    source_lines = [f"    q{index} <= d{index};" for index in range(30)]
    block = {"block_id": "rtl::blk::m::always::1-30", "kind": "always",
             "construct_type": "ALWAYS_FF", "original_name": "always@1",
             "file": "m.sv", "start_line": 1, "end_line": 30,
             "source_code": "\n".join(source_lines), "signals_read": ["d0"],
             "signals_written": ["q0"]}
    rtl = {"modules": {"m": _module("m", tmp_path / "m.sv", always_blocks=[block])}}

    windows = [record for record in re_.retrieval_records(rtl)
               if record["source_type"] == "construct"]

    assert windows[0]["chunk_id"] == "rtl:construct:m:always:1-30"
    assert [record["chunk_id"] for record in windows[1:]] == [
        f"rtl:construct:m:always:1-30:w{index}" for index in range(1, len(windows))]
    assert all(len(record["text"]) <= 120 for record in windows)
    embedded = [line.strip() for record in windows for line in record["text"].split("\n")]
    assert all(embedded.count(line.strip()) == 1 for line in source_lines)


def test_module_facets_overflow_into_windows_without_losing_a_port_or_comment(
        tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RTL_CHUNK_CHARS", 150)
    ports = [_declaration("wide", f"port_{index}", "PORT", "input") for index in range(40)]
    source = "module wide;\n" + "".join(f"// note {index}\n" for index in range(40))
    rtl = {"modules": {"wide": _module("wide", tmp_path / "wide.sv", ports=ports,
                                       source_code=source)}}

    windows = [record for record in re_.retrieval_records(rtl)
               if record["module"] == "wide" and record["source_type"] == "module"]
    text = "\n".join(record["text"] for record in windows)

    assert windows[0]["chunk_id"] == "rtl:wide" and len(windows) > 1
    assert all(len(record["text"]) <= 150 for record in windows)
    assert all(f"port_{index}" in text for index in range(40))
    assert all(f"note {index}" in text for index in range(40))
    assert all(not record["lookup_terms"] and "signal_names" not in record
               for record in windows[1:])


def test_every_port_name_is_expanded_into_terms(tmp_path):
    ports = [_declaration("wide", f"addr_{index}", "PORT", "input") for index in range(20)]
    rtl = {"modules": {"wide": _module("wide", tmp_path / "wide.sv", ports=ports)}}

    text = "\n".join(record["text"] for record in re_.retrieval_records(rtl))

    assert all(f"address {index}" in text for index in range(20))
