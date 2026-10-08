"""Focused contract tests for the module-centric Verilator projection."""

import json
import xml.etree.ElementTree as ET

import pytest

import rtl_ingest as ri


_XML = """<?xml version="1.0" encoding="UTF-8"?>
<verilator_xml>
  <files><file id="d" filename="design.sv" language="1800-2017"/></files>
  <cells>
    <cell loc="d,1,8,1,14" name="top" submodname="parent__W8" hier="chip.top">
      <cell loc="d,20,9,20,16" name="u_child" submodname="child"
            hier="chip.top.GEN[0].u_child"/>
    </cell>
  </cells>
  <netlist>
    <module loc="d,1,8,1,14" name="parent__W8" origName="parent" topModule="1">
      <var loc="d,2,15,2,18" name="clk" dir="input" dtype_id="1" vartype="logic"/>
      <var loc="d,3,21,3,25" name="DataI" dir="input" dtype_id="2" vartype="logic"/>
      <var loc="d,4,22,4,28" name="ResultO" dir="output" dtype_id="2" vartype="logic"/>
      <var loc="d,5,15,5,18" name="tmp" dtype_id="2" vartype="logic"/>
      <var loc="d,5,32,5,36" name="BUSY" dtype_id="2" vartype="logic"/>
      <var loc="d,6,17,6,22" name="WIDTH" dtype_id="3" vartype="int" param="true">
        <const loc="d,6,25,6,26" name="32'h8" dtype_id="3"/>
      </var>
      <func loc="d,8,3,8,11" name="mix_data">
        <begin loc="d,8,12,10,5"/>
      </func>
      <begin loc="d,11,3,11,11" name="GEN[0]">
        <always loc="d,12,3,12,12">
          <sentree loc="d,12,13,12,14">
            <senitem loc="d,12,15,12,25" edgeType="POS">
              <varref loc="d,12,23,12,25" name="clk" dtype_id="1"/>
            </senitem>
          </sentree>
          <assigndly loc="d,14,9,14,11">
            <varref loc="d,14,12,14,16" name="DataI" dtype_id="2"/>
            <varref loc="d,14,5,14,7" name="tmp" dtype_id="2"/>
          </assigndly>
        </always>
        <contassign loc="d,17,18,17,19">
          <varref loc="d,17,20,17,22" name="tmp" dtype_id="2"/>
          <varref loc="d,17,10,17,16" name="ResultO" dtype_id="2"/>
        </contassign>
        <contassign loc="d,18,18,18,19">
          <varref loc="d,18,20,18,24" name="DataI" dtype_id="2"/>
          <varref loc="d,18,10,18,16" name="ResultO" dtype_id="2"/>
        </contassign>
        <instance loc="d,20,9,20,16" name="u_child" defName="child" origName="u_child">
          <port loc="d,21,6,21,9" name="din" direction="in">
            <varref loc="d,21,11,21,13" name="tmp" dtype_id="2"/>
          </port>
          <port loc="d,22,6,22,10" name="dout" direction="out">
            <varref loc="d,22,12,22,18" name="ResultO" dtype_id="2"/>
          </port>
        </instance>
      </begin>
    </module>
    <module loc="d,1,8,1,14" name="parent__W16" origName="parent">
      <always loc="d,12,3,12,12">
        <assigndly loc="d,14,9,14,11">
          <varref loc="d,14,12,14,16" name="DataI" dtype_id="2"/>
          <varref loc="d,14,5,14,7" name="tmp" dtype_id="2"/>
        </assigndly>
      </always>
      <contassign loc="d,17,18,17,19">
        <varref loc="d,17,20,17,22" name="tmp" dtype_id="2"/>
        <varref loc="d,17,10,17,16" name="ResultO" dtype_id="2"/>
      </contassign>
    </module>
    <module loc="d,25,8,25,13" name="child" origName="child">
      <var loc="d,26,15,26,18" name="din" dir="input" dtype_id="2" vartype="logic"/>
      <var loc="d,27,16,27,20" name="dout" dir="output" dtype_id="2" vartype="logic"/>
    </module>
  </netlist>
  <typetable>
    <basicdtype id="1" name="logic"/>
    <basicdtype id="2" name="logic" left="7" right="0"/>
    <basicdtype id="3" name="int"/>
    <enumdtype id="4" name="pkg_demo::state_t" sub_dtype_id="2">
      <enumitem loc="d,31,3,31,8" name="IDLE" dtype_id="2">
        <const loc="d,31,12,31,17" name="8'h0" dtype_id="2"/>
      </enumitem>
      <enumitem loc="d,32,3,32,8" name="BUSY" dtype_id="2">
        <const loc="d,32,12,32,17" name="8'h1" dtype_id="2"/>
      </enumitem>
    </enumdtype>
    <enumdtype id="5" name="bare_t" sub_dtype_id="1">
      <enumitem loc="d,35,3,35,6" name="OFF" dtype_id="1"/>
    </enumdtype>
  </typetable>
</verilator_xml>
"""


def design(tmp_path):
    lines = [
        "module parent;",                    # 1
        "  input logic clk;",                # 2
        "  input logic [7:0] DataI;",        # 3
        "  output logic [7:0] ResultO;",     # 4
        "  logic [7:0] tmp; logic [7:0] BUSY;",  # 5
        "  parameter int WIDTH = 8;",        # 6
        "",                                  # 7
        "  function automatic mix_data;",   # 8
        "    // mixes incoming data",        # 9
        "  endfunction",                     # 10
        "  if (WIDTH) begin: GEN",           # 11
        "  always_ff @(posedge clk)",        # 12
        "  begin // IDLE BUSY",              # 13
        "    tmp <= DataI;",                 # 14
        "  end",                             # 15
        "",                                  # 16
        "  assign ResultO = tmp;",           # 17
        "  assign ResultO = DataI;",         # 18
        "  end",                             # 19
        "  child u_child (",                  # 20
        "    .din(tmp),",                    # 21
        "    .dout(ResultO));",              # 22
        "endmodule",                         # 23
        "",                                  # 24
        "module child;",                     # 25
        "  input logic [7:0] din;",          # 26
        "  output logic [7:0] dout;",        # 27
        "endmodule",                         # 28
        "",                                  # 29
        "package pkg_demo;",                 # 30
        "  typedef enum logic [7:0] { IDLE = 8'h0,",  # 31
        "    BUSY = 8'h1 } state_t;",        # 32
        "",                                  # 33
        "  typedef enum logic {",            # 34
        "    OFF } bare_t;",                 # 35
        "endpackage",                        # 36
    ]
    (tmp_path / "design.sv").write_text("\n".join(lines) + "\n")
    xml_path = tmp_path / "design.xml"
    xml_path.write_text(_XML)
    return xml_path


def built(tmp_path, monkeypatch):
    xml_path = design(tmp_path)
    monkeypatch.setattr(ri.cfg, "RTL_DIR", tmp_path)
    return ri.build(xml_path, "%Warning-WIDTH: design.sv:18:2: duplicate demo driver\n")


def test_build_emits_only_the_versioned_module_contract(tmp_path, monkeypatch):
    artifact = built(tmp_path, monkeypatch)

    assert list(artifact) == ["schema_version", "top_module", "tree", "modules", "enums",
                              "warnings"]
    assert artifact["schema_version"] == ri.SCHEMA_VERSION == 4
    assert artifact["top_module"] == "parent"
    assert "graph" not in artifact and "symbol_records" not in artifact
    assert not hasattr(ri, "hierarchy_graph")


def test_package_enums_keep_member_values_and_declaration_sites(tmp_path, monkeypatch):
    enums = {enum["original_name"]: enum for enum in built(tmp_path, monkeypatch)["enums"]}

    assert list(enums) == ["bare_t", "state_t"]
    state = enums["state_t"]
    assert state["enum_id"] == "rtl::enum::pkg_demo::state_t"
    assert state["package"] == "pkg_demo"
    assert state["normalized_name"] == "state_t"
    assert [item["original_name"] for item in state["items"]] == ["IDLE", "BUSY"]
    assert [item["value"] for item in state["items"]] == [0, 1]
    assert state["items"][0]["value_source"] == "8'h0"
    assert state["items"][0]["line"] == 31 and state["items"][1]["line"] == 32
    assert state["items"][0]["file"].endswith("design.sv")


def test_enum_members_carry_no_usage_sites(tmp_path, monkeypatch):
    enums = built(tmp_path, monkeypatch)["enums"]

    assert not any("usage_sites" in item for enum in enums for item in enum["items"])


def test_package_enums_tolerate_a_missing_package_and_missing_member_value(
        tmp_path, monkeypatch):
    enums = {enum["original_name"]: enum for enum in built(tmp_path, monkeypatch)["enums"]}
    bare = enums["bare_t"]

    assert bare["enum_id"] == "rtl::enum::bare_t"
    assert bare["package"] is None
    assert bare["items"] == [{
        "original_name": "OFF", "normalized_name": "off",
        "value": None, "value_source": None,
        "file": bare["items"][0]["file"], "line": 35,
    }]


def test_modules_are_deduplicated_normalized_and_source_complete(tmp_path, monkeypatch):
    modules = built(tmp_path, monkeypatch)["modules"]
    module = modules["parent"]

    # Parameter clones parent__W8 and parent__W16 collapse into one source module.
    assert not any(name.startswith("parent__") for name in modules)

    required = {
        "original_name", "normalized_name", "normalized_tokens", "file", "start_line",
        "end_line", "ports", "signals", "instances", "functions_tasks", "always_blocks",
        "assignment_groups", "source_code",
    }
    assert required <= set(module)
    assert module["normalized_name"] == "parent"
    assert module["start_line"] == 1 and module["end_line"] == 24
    assert module["source_code"].startswith("module parent;")
    assert module["source_code"].rstrip().endswith("endmodule")


def test_signal_metadata_has_compiler_type_width_and_deterministic_sites(tmp_path, monkeypatch):
    module = built(tmp_path, monkeypatch)["modules"]["parent"]
    ports = {port["original_name"]: port for port in module["ports"]}
    signals = {signal["original_name"]: signal for signal in module["signals"]}

    assert ports["DataI"]["direction"] == "input"
    assert ports["DataI"]["data_type"] == "logic"
    assert ports["DataI"]["width"] == 8
    assert ports["DataI"]["width_expression"] == "[7:0]"
    assert ports["DataI"]["normalized_name"] == "data_i"
    assert ports["DataI"]["reader_sites"] == [
        {"module": "parent", "file": "design.sv", "line": 14},
        {"module": "parent", "file": "design.sv", "line": 18},
    ]
    assert signals["tmp"]["writer_sites"] == [
        {"module": "parent", "file": "design.sv", "line": 14}
    ]
    assert signals["tmp"]["reader_sites"] == [
        {"module": "parent", "file": "design.sv", "line": 17}
    ]


def test_only_useful_structural_blocks_are_retained_and_assignments_are_grouped(
        tmp_path, monkeypatch):
    module = built(tmp_path, monkeypatch)["modules"]["parent"]

    assert len(module["always_blocks"]) == 1
    assert module["always_blocks"][0]["construct_type"] == "ALWAYS_FF"
    assert module["always_blocks"][0]["signals_read"] == ["clk", "DataI"]
    assert module["always_blocks"][0]["signals_written"] == ["tmp"]
    assert len(module["functions_tasks"]) == 1
    assert module["functions_tasks"][0]["original_name"] == "mix_data"
    assert len(module["assignment_groups"]) == 1
    group = module["assignment_groups"][0]
    assert (group["start_line"], group["end_line"], len(group["assignments"])) == (17, 18, 2)
    assert group["signals_read"] == ["tmp", "DataI"]
    assert group["signals_written"] == ["ResultO"]


def test_instances_keep_ast_formal_actual_bindings_and_hierarchy(tmp_path, monkeypatch):
    artifact = built(tmp_path, monkeypatch)
    instance = artifact["modules"]["parent"]["instances"][0]
    bindings = {binding["formal_name"]: binding for binding in instance["bindings"]}

    assert instance["target_module"] == "child"
    assert bindings["din"]["formal_signal_id"] == "rtl::port::child::din"
    assert bindings["din"]["actual_signal_ids"] == ["rtl::sig::parent::tmp"]
    assert bindings["din"]["direction"] == "input"
    assert bindings["dout"]["actual_signal_ids"] == ["rtl::port::parent::ResultO"]
    child = artifact["tree"][1]
    assert child["hierarchical_path"] == ["chip", "top", "GEN[0]", "u_child"]


def test_artifact_is_byte_deterministic(tmp_path, monkeypatch):
    first = built(tmp_path, monkeypatch)
    second = built(tmp_path, monkeypatch)

    assert json.dumps(first, ensure_ascii=False, sort_keys=False) == json.dumps(
        second, ensure_ascii=False, sort_keys=False)


def test_lint_warnings_remain_structured(tmp_path, monkeypatch):
    assert built(tmp_path, monkeypatch)["warnings"] == [{
        "code": "WIDTH", "file": "design.sv", "line": 18,
        "message": "duplicate demo driver",
    }]


# Fields removed in the 2026-09-23 rtl_ingest walk, by the record that carried them.
REMOVED_FIELDS = {
    "binding": {"formal", "actual_refs", "formal_symbol_id", "actual_symbol_ids",
                "port_index", "hierarchical_path", "hierarchical_paths", "instance_id",
                "instance", "binding_id"},
    "instance": {"target_elaborated_module", "target_elaborated_modules",
                 "hierarchical_paths", "parent_construct"},
    "declaration": {"dtype_id", "elaborated_modules", "parent_construct"},
    "module": {"elaborated_modules"},
    "block": {"clock", "reset"},
    "tree": {"elaborated_module", "loc", "parent_hier"},
}


def _records(artifact):
    """Yield (kind, record) for every record type in an rtl.json payload."""
    for node in artifact["tree"]:
        yield "tree", node
    for module in artifact["modules"].values():
        yield "module", module
        for key in ("ports", "signals", "parameters"):
            for declaration in module[key]:
                yield "declaration", declaration
        for key in ("always_blocks", "functions_tasks", "assignment_groups", "other_blocks"):
            for block in module[key]:
                yield "block", block
                for assignment in block.get("assignments", []):
                    yield "block", assignment
        for instance in module["instances"]:
            yield "instance", instance
            for binding in instance["bindings"]:
                yield "binding", binding
    for enum in artifact["enums"]:
        yield "enum", enum


def test_records_carry_no_removed_fields(tmp_path, monkeypatch):
    for kind, record in _records(built(tmp_path, monkeypatch)):
        assert not (REMOVED_FIELDS.get(kind, set()) | {"content_hash"}) & set(record), kind


def test_rtl_ingest_does_not_import_the_spec_stage():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(ri.__file__).read_text(encoding="utf-8"))
    assert "ingest" not in {node.module for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom)}


def _values(node, keys):
    """Yield every value stored under one of ``keys`` anywhere in a JSON tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys:
                yield value
            yield from _values(value, keys)
    elif isinstance(node, list):
        for item in node:
            yield from _values(item, keys)


def test_source_paths_are_posix_relative_to_the_rtl_dir(tmp_path, monkeypatch):
    artifact = built(tmp_path, monkeypatch)
    xml_path = tmp_path / "design.xml"
    absolute = ri.build(xml_path, f"%Warning-WIDTH: {tmp_path}/design.sv:18:2: demo\n")

    paths = set(_values(artifact, {"file", "file_path"}))

    assert paths == {"design.sv"}
    assert absolute["warnings"][0]["file"] == "design.sv"


def test_a_module_cites_the_header_above_it_back_to_the_previous_module(tmp_path, monkeypatch):
    source = tmp_path / "pair.sv"
    source.write_text("// Pair of stages.\n// First stage adds.\nmodule first;\nendmodule\n"
                      "// Second stage subtracts.\nmodule second;\nendmodule\n")
    extents = {"first": ("a", 3, 5), "second": ("a", 6, 7)}

    assert ri.module_preamble(extents, "first", source) == (
        "// Pair of stages.\n// First stage adds.\n")
    # The previous module's extent already runs up to this one's start line.
    assert ri.module_preamble(extents, "second", source) == ""
    assert built(tmp_path, monkeypatch)["modules"]["parent"]["preamble"] == ""


def _write_design(directory, files):
    """Write SystemVerilog sources into a temporary RTL directory."""
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")


def test_design_sources_finds_the_uninstantiated_top_and_every_package(tmp_path):
    _write_design(tmp_path, {
        "bus_pkg.sv": "package bus_pkg;\n  typedef logic [7:0] byte_t;\nendpackage\n",
        "defs.sv": "`define WIDTH 8\n",
        "child.sv": "module child(input logic a);\nendmodule\n",
        "soc.sv": ("// soc wraps child\nmodule soc(input logic a);\n"
                   "  child u_child(.a(a));\nendmodule : soc\n"),
        "notes.sv": "// soc is the top of this design\nmodule helper;\n  soc u_soc();\nendmodule\n",
        "chip.sv": "module chip;\n  helper u_helper();\nendmodule\n",
    })

    sources, top = ri.design_sources(tmp_path)

    assert [path.name for path in sources] == ["bus_pkg.sv", "chip.sv"]
    assert top == "chip"


def test_design_sources_asks_for_a_top_when_several_modules_are_uninstantiated(tmp_path):
    _write_design(tmp_path, {
        "a.sv": "module a;\nendmodule\n",
        "b.sv": "module b;\nendmodule\n",
    })

    with pytest.raises(SystemExit, match="RTL_TOP_MODULE"):
        ri.design_sources(tmp_path)


def test_design_sources_honours_a_configured_top(tmp_path):
    _write_design(tmp_path, {
        "a.sv": "module a;\nendmodule\n",
        "b.sv": "module b;\nendmodule\n",
    })

    assert ri.design_sources(tmp_path, top="b") == ([tmp_path / "b.sv"], "b")


def test_design_sources_reads_plain_verilog_files(tmp_path):
    _write_design(tmp_path, {
        "alu.v": "module alu(input a);\nendmodule\n",
        "core.v": "module core(input a);\n  alu u_alu(.a(a));\nendmodule\n",
    })

    assert ri.design_sources(tmp_path) == ([tmp_path / "core.v"], "core")


def test_file_table_maps_verilog_and_header_sources(tmp_path):
    for name in ("core.v", "top.sv", "defs.svh", "regs.vh"):
        (tmp_path / name).write_text("", encoding="utf-8")
    root = ET.fromstring(
        '<verilator_xml><files>'
        '<file id="a" filename="core.v"/><file id="b" filename="top.sv"/>'
        '<file id="c" filename="defs.svh"/><file id="d" filename="regs.vh"/>'
        '<file id="e" filename="&lt;built-in&gt;"/></files></verilator_xml>')

    assert {key: path.name for key, path in ri.file_table(root, tmp_path).items()} == {
        "a": "core.v", "b": "top.sv", "c": "defs.svh", "d": "regs.vh"}
