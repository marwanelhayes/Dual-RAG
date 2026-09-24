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
            "original_name": name,
            "width": width,
            "direction": direction,
            "description": description,
            "document_id": "spec::doc::isa",
            "section_id": "spec::section::fpu",
            "section_title": "Floating Point Unit",
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
                         "original_name": name, "normalized_name": None,
                         "width": (widths or {}).get(name)} for name in names]}


def test_a_spec_signal_links_to_the_rtl_signal_with_the_same_flat_name():
    rtl = _rtl(("fpu", _module("fpu", "rtl::module::fpu", [
        _rtl_signal("rm_i", "rtl::signal::rm"), _rtl_signal("opcode_i", "rtl::signal::op")])))

    links = _links(_spec(), rtl)

    assert links == [("rounding_mode", "rm_i", "fpu", "identity", None)]


def test_full_artifact_maps_sections_from_title_context_and_child_signals():
    rtl = _rtl(
        ("fpu", _module("fpu", "rtl::module::fpu", [
            _rtl_signal("rm_i", "rtl::signal::rm")], original_name="fpu")),
        ("decoder", _module("decoder", "rtl::module::decoder", [
            _rtl_signal("opcode_i", "rtl::signal::opcode", 7, "input", "opcode")]))
    )

    artifact = bridge.build_bridge(_spec(), rtl, _hierarchy())

    assert set(artifact) == {
        "schema_version", "source_fingerprints", "signal_alignments",
        "unresolved_signals", "module_alignments", "summary",
    }
    assert artifact["signal_alignments"][0]["rtl_signal_id"] == "rtl::signal::rm"
    module = artifact["module_alignments"][0]
    assert module["rtl_module_id"] == "rtl::module::fpu"
    assert "normalized_title" in module["methods"]
    assert "child_signal_support" in module["methods"]
    serialized = json.dumps(artifact)
    for behavioral_verdict in ("IMPLEMENTED", "PARTIALLY", "MISSING", "CONTRADICTED"):
        assert behavioral_verdict not in serialized
    assert artifact == bridge.build_bridge(_spec(), rtl, _hierarchy())


def test_module_alignment_normalizes_plural_and_container_nouns():
    rtl = _rtl(("cache", _module("cache", "rtl::module::cache", [])))
    hierarchy = {"documents": [{"document_id": "axi", "sections": [{
        "node_id": "axi::caches", "section_name": "Caches", "path": [],
        "doc_name": "axi.pdf", "start_page": 1,
    }]}]}

    artifact = bridge.build_bridge({"signals": []}, rtl, hierarchy)

    assert artifact["module_alignments"][0]["rtl_module_name"] == "cache"
    assert artifact["module_alignments"][0]["confidence_level"] in {
        bridge.MEDIUM, bridge.STRONG,
    }


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


def _threaded_rtl():
    """Return an artifact where one port is threaded through two hierarchy levels."""
    port = {"original_name": "MAWBurst", "direction": "output", "width": 2}
    return {"modules": {
        "riscv_processor": {
            "original_name": "riscv_processor", "file": "rtl/riscv_processor.sv",
            "ports": [], "instances": [{"target_module": "fetch_stage", "bindings": [
                {"parent_module": "riscv_processor", "formal_name": "MAWBurst",
                 "actual_signals": ["IMAWBurst"], "direction": "output"}]}]},
        "fetch_stage": {
            "original_name": "fetch_stage", "file": "rtl/fetch_stage.sv",
            "ports": [dict(port)], "instances": [{"target_module": "cache", "bindings": [
                {"parent_module": "fetch_stage", "formal_name": "MAWBurst",
                 "actual_signals": ["MAWBurst"], "direction": "output"}]}]},
        "cache": {"original_name": "cache", "file": "rtl/cache.sv",
                  "ports": [dict(port)], "instances": []},
    }}


def test_a_port_threaded_through_the_hierarchy_is_one_net():
    nets = bridge.port_nets(_threaded_rtl())

    assert nets[("cache", "MAWBurst")] == nets[("fetch_stage", "MAWBurst")]


def test_an_unbound_port_belongs_to_no_shared_net():
    rtl = _threaded_rtl()
    rtl["modules"]["cache"]["ports"].append(
        {"original_name": "PrivateState", "direction": "output", "width": 1})

    assert ("cache", "PrivateState") not in bridge.port_nets(rtl)


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
