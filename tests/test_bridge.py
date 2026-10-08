"""Hermetic tests for deterministic specification-to-RTL identity alignment."""

import json

import pytest

import bridge
import configure as cfg


def _spec(name="rounding_mode", width=3, direction="input", description="select rounding mode"):
    return {
        "schema_version": "1.0",
        "signals": [{
            "signal_id": f"spec::signal::{name}",
            "name": name,
            "width": width,
            "description": description,
            "document_id": "spec::doc::isa",
            "hierarchy_id": "spec::section::fpu",
            "section_path": ["Floating Point Unit"],
            "table_id": "table::interface",
            "chunk_id": "chunk::fpu",
            "page": 12,
            "source_file": "spec.pdf",
        }],
    }


def _rtl_signal(name, signal_id, width=3, direction="input", description="select rounding mode"):
    return {
        "signal_id": signal_id,
        "original_name": name,
        "width": width,
        "direction": direction,
        "description": description,
        "file_path": "rtl/fpu.sv",
        "start_line": 7,
        "end_line": 7,
        "reader_sites": [{"file": "rtl/fpu.sv", "line": 19}],
        "writer_sites": [],
    }


def _module(name, module_id, signals, original_name=None):
    return {
        "module_id": module_id,
        "original_name": original_name or name,
        "file": f"rtl/{name}.sv",
        "start_line": 1,
        "end_line": 40,
        "ports": signals,
        "signals": [],
        "always_blocks": [],
        "functions_tasks": [],
        "assignment_groups": [],
        "other_blocks": [],
    }


def _rtl(*modules):
    return {"schema_version": "1.0", "modules": {
        name: module for name, module in modules
    }}


def _hierarchy():
    return {
        "schema_version": "1.0",
        "documents": [{
            "document_id": "spec::doc::isa",
            "source_file": "spec.pdf",
            "sections": [{
                "node_id": "spec::section::fpu",
                "section_name": "Floating Point Unit",
                "subsection_title": None,
                "path": ["Floating Point Unit"],
                "doc_name": "spec.pdf",
                "document_id": "spec::doc::isa",
                "start_page": 12,
            }],
        }],
    }


def _links(spec, rtl):
    """Return ``(spec name, rtl name, module, method, prefix)`` for every link."""
    artifact = bridge.build_bridge(spec, rtl, _hierarchy())
    return sorted((row["spec_original_name"], row["rtl_original_name"], row["rtl_module_name"],
                   row["method"], row["prefix"]) for row in artifact["signal_alignments"])


def _specs(*names, widths=None):
    """Return a spec artifact with one port row per name."""
    rows = _spec()["signals"][0]
    return {"signals": [{**rows, "signal_id": f"spec::signal::axi::A1::{name.lower()}",
                         "name": name, "normalized_name": None,
                         "width": (widths or {}).get(name)} for name in names]}


def test_a_spec_signal_links_to_the_rtl_signal_with_the_same_flat_name():
    rtl = _rtl(("fpu", _module("fpu", "rtl::module::fpu", [
        _rtl_signal("rm_i", "rtl::signal::rm"), _rtl_signal("opcode_i", "rtl::signal::op")])))

    links = _links(_spec(), rtl)

    assert links == [("rounding_mode", "rm_i", "fpu", "identity", None)]


def test_a_section_links_to_the_modules_its_own_signals_link_to():
    rtl = _rtl(
        ("fpu_core", _module("fpu_core", "rtl::module::fpu_core", [
            _rtl_signal("rm_i", "rtl::signal::rm")])),
        ("decoder", _module("decoder", "rtl::module::decoder", [
            _rtl_signal("opcode_i", "rtl::signal::opcode", 7, "input", "opcode")])))

    artifact = bridge.build_bridge(_spec(), rtl, _hierarchy())

    assert set(artifact) == {
        "schema_version", "source_fingerprints", "signal_alignments",
        "unresolved_signals", "module_alignments", "summary",
    }
    assert [(row["rtl_module_name"], row["method"], row["link_count"])
            for row in artifact["module_alignments"]] == [("fpu_core", "link_support", 1)]
    serialized = json.dumps(artifact)
    for behavioral_verdict in ("IMPLEMENTED", "PARTIALLY", "MISSING", "CONTRADICTED"):
        assert behavioral_verdict not in serialized
    assert artifact == bridge.build_bridge(_spec(), rtl, _hierarchy())


def test_a_section_with_no_title_identity_or_linked_signal_is_not_written():
    rtl = _rtl(("pc_adder", _module("pc_adder", "rtl::module::pc_adder", [])))
    hierarchy = {"documents": [{"document_id": "axi", "sections": [{
        "node_id": "axi::pcie", "section_name": "PCIe XT mode", "path": ["PCIe XT mode"],
        "doc_name": "axi.pdf", "start_page": 1,
    }]}]}

    assert bridge.build_bridge({"signals": []}, rtl, hierarchy)["module_alignments"] == []

def test_module_alignment_normalizes_plural_and_container_nouns():
    rtl = _rtl(("cache", _module("cache", "rtl::module::cache", [])))
    hierarchy = {"documents": [{"document_id": "axi", "sections": [{
        "node_id": "axi::caches", "section_name": "Caches", "path": [],
        "doc_name": "axi.pdf", "start_page": 1,
    }]}]}

    artifact = bridge.build_bridge({"signals": []}, rtl, hierarchy)

    assert artifact["module_alignments"][0]["rtl_module_name"] == "cache"
    assert artifact["module_alignments"][0]["method"] == "title_identity"
    assert artifact["module_alignments"][0]["confidence_level"] == bridge.STRONG


def test_inputs_are_read_by_their_schema_keys_only():
    with pytest.raises(KeyError):
        bridge.spec_signals({"records": _spec()["signals"]})
    assert bridge.rtl_signals(_rtl(("fpu", _module("fpu", "rtl::module::fpu", [
        _rtl_signal("rm_i", "rtl::signal::rm")]))))[0]["file"] == "rtl/fpu.sv"

def test_source_fingerprints_hash_the_three_current_inputs(tmp_path, monkeypatch):
    paths = {}
    for name, text in (("SPEC_SIGNALS_PATH", "spec"), ("RTL_PATH", "rtl"),
                       ("SPEC_HIERARCHY_PATH", "hierarchy")):
        path = tmp_path / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(cfg, name, path)
        paths[name] = path

    first = bridge.source_fingerprints()
    paths["RTL_PATH"].write_text("changed rtl", encoding="utf-8")
    second = bridge.source_fingerprints()

    assert set(first) == {"spec_signals", "rtl", "spec_hierarchy"}
    assert len(set(first.values())) == 3
    assert first["spec_signals"] == second["spec_signals"]
    assert first["rtl"] != second["rtl"]


def test_nested_ingest_hierarchy_keeps_each_subsection_identity_and_own_title():
    hierarchy = {"documents": [{"document_id": "doc", "sections": [
        {"node_id": "sec::root", "section_id": "sec::root", "path": ["Floating Point Unit"],
         "section_title": "Floating Point Unit", "section_name": "Floating Point Unit",
         "doc_name": "doc.pdf", "start_page": 1},
        {"node_id": "sec::round", "section_id": "sec::root",
         "path": ["Floating Point Unit", "Rounding Control"],
         "section_title": "Floating Point Unit", "subsection_id": "sec::round",
         "subsection_title": "Rounding Control", "section_name": "Rounding Control",
         "doc_name": "doc.pdf", "start_page": 2},
    ]}]}

    sections = [bridge._canonical_section(row) for row in bridge._section_rows(hierarchy)]

    assert [(section["section_id"], section["title"]) for section in sections] == [
        ("sec::root", "Floating Point Unit"),
        ("sec::round", "Rounding Control"),
    ]


def test_the_bridge_has_no_net_propagation():
    assert not hasattr(bridge, "port_nets")


def test_the_bridge_has_no_tiny_model_resolver_or_lookup_helpers():
    assert not any(hasattr(bridge, name) for name in (
        "ollama_resolver", "ambiguity_evidence", "lookup_signal", "lookup_module"))


def test_the_bridge_infers_no_module_roles():
    assert not any(hasattr(bridge, name) for name in (
        "module_roles", "with_module_roles", "expected_direction", "_source_role"))


def test_the_bridge_writes_no_enum_alignment():
    artifact = bridge.build_bridge(_spec(), _rtl(), _hierarchy())

    assert "enum_alignments" not in artifact
    assert not any(key.startswith("enum") for key in artifact["summary"])


def _port(name, width=1, direction="output"):
    return _rtl_signal(name, f"rtl::port::cache::{name}", width, direction, "")


def test_a_prefix_shared_by_a_modules_ports_links_them_to_spec_names():
    rtl = _rtl(("cache", _module("cache", "rtl::module::cache", [
        _port("MAWValid"), _port("MAWAddr", 32)])))

    links = _links(_specs("AWVALID", "AWADDR"), rtl)

    assert links == [("AWADDR", "MAWAddr", "cache", "interface_prefix", "m"),
                     ("AWVALID", "MAWValid", "cache", "interface_prefix", "m")]


def test_a_lone_prefixed_port_is_not_linked():
    rtl = _rtl(("cache", _module("cache", "rtl::module::cache", [
        _port("MAWValid"), _port("Ready")])))

    assert _links(_specs("AWVALID"), rtl) == []


def test_a_port_links_to_the_longest_spec_name_behind_its_prefix():
    rtl = _rtl(("cache", _module("cache", "rtl::module::cache", [
        _port("MARValid"), _port("MARAddr", 32), _port("MRRValid", direction="input"),
        _port("MRRData", 32, "input")])))

    links = _links(_specs("ARVALID", "ARADDR", "RVALID", "RDATA"), rtl)

    assert ("ARVALID", "MARValid", "cache", "interface_prefix", "m") in links
    assert ("RVALID", "MARValid", "cache", "interface_prefix", "ma") not in links
    assert ("RVALID", "MRRValid", "cache", "interface_prefix", "mr") in links


def test_a_width_mismatch_is_stated_not_rejected():
    rtl = _rtl(("cache", _module("cache", "rtl::module::cache", [
        _port("MARSize", 4), _port("MAWSize", 4)])))

    artifact = bridge.build_bridge(
        _specs("ARSIZE", "AWSIZE", widths={"ARSIZE": 3, "AWSIZE": "SIZE_WIDTH"}), rtl,
        _hierarchy())
    widths = {row["spec_original_name"]: row["width"] for row in artifact["signal_alignments"]}

    assert widths["ARSIZE"] == {"spec": 3, "rtl": 4, "status": "conflict"}
    assert widths["AWSIZE"]["status"] == "symbolic"


def test_a_spec_signal_no_rtl_signal_states_is_unresolved():
    rtl = _rtl(("fpu", _module("fpu", "rtl::module::fpu", [_rtl_signal("opcode_i", "op")])))

    artifact = bridge.build_bridge(_specs("AWNSAID"), rtl, _hierarchy())

    assert artifact["signal_alignments"] == []
    assert artifact["unresolved_signals"][0]["confidence_level"] == bridge.UNRESOLVED


def test_a_renamed_wire_on_a_linked_net_is_not_linked():
    cache = _module("cache", "rtl::module::cache", [_port("MAWBurst", 2), _port("MAWValid")])
    stage = _module("fetch_stage", "rtl::module::fetch_stage", [
        _rtl_signal("burst_to_bus", "rtl::port::fetch_stage::burst_to_bus", 2, "output", "")])
    stage["instances"] = [{"target_module": "cache", "bindings": [
        {"parent_module": "fetch_stage", "formal_name": "MAWBurst",
         "actual_signals": ["burst_to_bus"], "direction": "output"}]}]
    rtl = _rtl(("cache", cache), ("fetch_stage", stage))

    links = _links(_specs("AWBURST", "AWVALID"), rtl)

    # Net propagation spread exact-name collisions (all 19 such links were wrong).
    assert ("AWBURST", "MAWBurst", "cache", "interface_prefix", "m") in links
    assert not any(link[1] == "burst_to_bus" for link in links)


def test_an_unresolved_signal_keeps_suggested_candidates_that_are_not_links():
    rtl = _rtl(("fpu", _module("fpu", "rtl::module::fpu", [
        _rtl_signal("round_mode_sel", "rtl::signal::round", 3, "input", ""),
        _rtl_signal("opcode_i", "rtl::signal::op", 7, "input", "")])))

    artifact = bridge.build_bridge(_specs("round_mode", widths={"round_mode": 3}), rtl,
                                   _hierarchy())
    candidates = artifact["unresolved_signals"][0]["candidates"]

    assert artifact["signal_alignments"] == []
    assert candidates[0]["rtl_original_name"] == "round_mode_sel"
    assert candidates[0]["status"] == "SUGGESTED" and 0 < candidates[0]["score"] <= 1
    assert candidates[0]["width"]["status"] == "equal"
    assert len(candidates) <= 3


def test_a_csr_links_to_the_enum_member_that_declares_it():
    rtl = _rtl(("csr_file", _module("csr_file", "rtl::module::csr_file", [])))
    rtl["enums"] = [{"enum_id": "rtl::enum::csr_pkg::csr_index_t", "original_name": "csr_index_t",
                     "package": "csr_pkg", "items": [
                         {"original_name": "mstatus", "value": 0, "file": "csr_defs.sv",
                          "line": 12}]}]

    artifact = bridge.build_bridge(_specs("mstatus"), rtl, _hierarchy())
    link = artifact["signal_alignments"][0]

    assert link["rtl_kind"] == "enum_member" and link["method"] == "identity"
    assert (link["rtl_enum_name"], link["rtl_member_name"], link["rtl_member_value"]) == (
        "csr_index_t", "mstatus", 0)
    assert link["provenance"]["rtl"]["file"] == "csr_defs.sv"
