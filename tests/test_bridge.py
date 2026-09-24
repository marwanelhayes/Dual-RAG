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
        "file": "rtl/fpu.sv",
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


def _instruction_spec(*names):
    return {"schema_version": "4", "signals": [{
        "signal_id": f"spec::instruction::isa::{name.lower().replace('.', '_')}",
        "original_name": name, "row_kind": "instruction",
        "document_id": "spec::doc::isa", "section_id": "spec::section::fpu",
        "section_title": "Floating Point Unit", "chunk_id": "chunk::fpu",
        "page": 119, "source_file": "spec.pdf",
    } for name in names]}


def _enum_rtl(*members):
    return {"schema_version": 3, "modules": {}, "enums": [{
        "enum_id": "rtl::enum::shared_pkg::fpu_operation_t",
        "original_name": "fpu_operation_t", "package": "shared_pkg",
        "normalized_name": "fpu_operation_t",
        "items": [{
            "original_name": name, "normalized_name": name.lower(),
            "value": value, "value_source": f"5'h{value:x}",
            "file": "rtl/shared_pkg.sv", "line": 239 + value,
            "usage_sites": ([{"module": "fpu_decoder", "construct_type": "ALWAYS_COMB",
                              "file": "rtl/fpu_decoder.sv", "line": 63}] if used else []),
        } for name, value, used in members],
    }]}


def test_instruction_rows_align_to_identically_named_enum_members():
    artifact = bridge.build_bridge(
        _instruction_spec("FADD.S", "FSQRT.S"),
        _enum_rtl(("FADD_S", 0, True), ("FSUB_S", 1, True)), _hierarchy())
    aligned = artifact["enum_alignments"]

    assert [row["spec_original_name"] for row in aligned] == ["FADD.S"]
    assert aligned[0]["rtl_member_name"] == "FADD_S"
    assert aligned[0]["rtl_member_value"] == 0
    assert aligned[0]["rtl_enum_name"] == "fpu_operation_t"
    assert aligned[0]["confidence_level"] in {bridge.STRONG, bridge.MEDIUM}
    assert artifact["summary"]["enum_alignments"] == 1
    assert artifact["summary"]["unaligned_instructions"] == 1


def test_enum_alignment_records_whether_the_member_is_implemented():
    artifact = bridge.build_bridge(
        _instruction_spec("FADD.S", "FSUB.S"),
        _enum_rtl(("FADD_S", 0, True), ("FSUB_S", 1, False)), _hierarchy())
    by_name = {row["spec_original_name"]: row for row in artifact["enum_alignments"]}

    assert by_name["FADD.S"]["component_evidence"]["implementation"]["score"] == 1.0
    assert by_name["FSUB.S"]["component_evidence"]["implementation"]["score"] == 0.0
    assert by_name["FADD.S"]["provenance"]["rtl"]["usage_sites"]


def test_instruction_rows_never_enter_signal_alignment():
    artifact = bridge.build_bridge(
        _instruction_spec("FADD.S"),
        _enum_rtl(("FADD_S", 0, True)), _hierarchy())

    assert artifact["signal_alignments"] == []
    assert artifact["unresolved_signals"] == []


def _hierarchy():
    return {
        "schema_version": "1.0",
        "documents": [{
            "document_id": "spec::doc::isa",
            "source_file": "spec.pdf",
            "sections": [{
                "section_id": "spec::section::fpu",
                "section_title": "Floating Point Unit",
                "parent_id": None,
                "children_ids": [],
                "prev_id": None,
                "next_id": None,
                "page": 12,
            }],
        }],
    }


def test_layer_one_keeps_multiple_candidates_and_exposes_abbreviation_evidence():
    spec = bridge.spec_signals(_spec())[0]
    rtl = _rtl(
        ("fpu", _module("fpu", "rtl::module::fpu", [
            _rtl_signal("rm_i", "rtl::signal::rm"),
            _rtl_signal("round_select_i", "rtl::signal::round_select"),
            _rtl_signal("operation_i", "rtl::signal::operation"),
        ])),
    )

    candidates = bridge.generate_signal_candidates(spec, bridge.rtl_signals(rtl))

    assert len(candidates) == 3
    rm = next(item for item in candidates if item["rtl"]["original_name"] == "rm_i")
    assert rm["cheap"]["abbreviation_expansion"] is True
    assert "abbreviation_expansion" in rm["cheap"]["methods"]


def test_layer_one_recognizes_known_namespace_prefix_without_suffix_false_positive():
    spec = bridge.spec_signals(_spec(
        "result", width=32, direction="output", description="calculated result"))[0]
    rtl = _rtl(("fpu", _module("fpu", "rtl::module::fpu", [
        _rtl_signal("fp_result_o", "rtl::signal::result", 32, "output"),
        _rtl_signal("invalid_o", "rtl::signal::invalid", 1, "output"),
    ])))

    candidates = bridge.generate_signal_candidates(spec, bridge.rtl_signals(rtl))
    by_name = {candidate["rtl"]["original_name"]: candidate for candidate in candidates}

    assert by_name["fp_result_o"]["cheap"]["namespace_prefix"] is True
    assert by_name["invalid_o"]["cheap"]["namespace_prefix"] is False


def test_structural_width_and_direction_choose_the_compatible_signal():
    spec = bridge.spec_signals(_spec("result", 32, "output", "calculated result"))[0]
    rtl = _rtl(
        ("fpu", _module("fpu", "rtl::module::fpu", [
            _rtl_signal("result_i", "rtl::signal::wrong", 16, "input", "input result"),
            _rtl_signal("result_o", "rtl::signal::right", 32, "output", "calculated result"),
            _rtl_signal("status_o", "rtl::signal::status", 1, "output", "status"),
        ])),
    )

    ranked = bridge.rerank_signal_candidates(
        spec, bridge.generate_signal_candidates(spec, bridge.rtl_signals(rtl)))

    assert ranked[0]["rtl"]["signal_id"] == "rtl::signal::right"
    assert ranked[0]["component_evidence"]["width"]["score"] == 1.0
    assert ranked[0]["component_evidence"]["direction"]["score"] == 1.0
    assert ranked[1]["component_evidence"]["width"]["score"] == 0.0


def test_semantic_scorer_sees_only_structural_survivors():
    spec = bridge.spec_signals(_spec())[0]
    rtl = _rtl(
        ("fpu", _module("fpu", "rtl::module::fpu", [
            _rtl_signal("rounding_mode", "rtl::signal::right"),
            _rtl_signal("unrelated_alpha", "rtl::signal::alpha", 8, "output", "other"),
            _rtl_signal("unrelated_beta", "rtl::signal::beta", 16, "output", "other"),
        ])),
    )
    seen = []

    def scorer(pairs):
        seen.extend(pairs)
        return [0.9] * len(pairs)

    ranked = bridge.rerank_signal_candidates(
        spec, bridge.generate_signal_candidates(spec, bridge.rtl_signals(rtl)), scorer=scorer)

    survivors = [item for item in ranked if item["survived_structural"]]
    assert len(seen) == len(survivors) == 1
    assert all(item["semantic_score"] is None for item in ranked if item not in survivors)


def test_close_top_candidates_remain_unresolved_without_tiny_model():
    spec = bridge.spec_signals(_spec())[0]
    rtl = _rtl(
        ("a", _module("a", "rtl::module::a", [
            _rtl_signal("rounding_mode", "rtl::signal::a")], original_name="fpu")),
        ("b", _module("b", "rtl::module::b", [
            _rtl_signal("rounding_mode", "rtl::signal::b")], original_name="fpu")),
    )

    outcome, record = bridge.align_signal(spec, bridge.rtl_signals(rtl))

    assert outcome == "unresolved"
    assert record["confidence_level"] == bridge.UNRESOLVED
    assert record["ambiguous"] is True
    assert [item["candidate_rank"] for item in record["candidates"][:2]] == [1, 2]


def test_low_quality_candidates_are_retained_but_not_linked():
    spec = bridge.spec_signals(_spec("architectural_flag", 1, "input", "architectural flag"))[0]
    rtl = _rtl(
        ("decoder", _module("decoder", "rtl::module::decoder", [
            _rtl_signal("writeback_data", "rtl::signal::writeback", 64, "output", "bus data"),
            _rtl_signal("opcode", "rtl::signal::opcode", 7, "internal", "decoded opcode"),
            _rtl_signal("clock_i", "rtl::signal::clock", 1, "input", "clock"),
        ])),
    )

    outcome, record = bridge.align_signal(spec, bridge.rtl_signals(rtl))

    assert outcome == "unresolved"
    assert record["confidence"] == 0.0
    assert record["candidates"]


def test_persisted_candidate_provenance_is_bounded():
    spec = bridge.spec_signals(_spec())[0]
    signal = _rtl_signal("rounding_mode", "rtl::signal::many-sites")
    signal["reader_sites"] = [
        {"file": "rtl/fpu.sv", "line": line} for line in range(10, 20)
    ]
    rtl = _rtl(("fpu", _module("fpu", "rtl::module::fpu", [signal])))

    _, record = bridge.align_signal(spec, bridge.rtl_signals(rtl))
    provenance = record["candidates"][0]["provenance"]

    assert provenance["reader_site_count"] == 10
    assert len(provenance["reader_sites"]) == cfg.BRIDGE_PROVENANCE_SITES


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
        "unresolved_signals", "enum_alignments", "module_alignments", "summary",
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
        "node_id": "axi::caches", "section_name": "Caches", "parent_id": None,
    }]}]}

    artifact = bridge.build_bridge({"signals": []}, rtl, hierarchy)

    assert artifact["module_alignments"][0]["rtl_module_name"] == "cache"
    assert artifact["module_alignments"][0]["confidence_level"] in {
        bridge.MEDIUM, bridge.STRONG,
    }


def test_schema_aliases_and_lookup_helpers_survive_upstream_migration(tmp_path):
    specs = {"records": [{
        "id": "legacy::rm", "display": "rounding_mode", "bit_width": "3",
        "dir": "input", "section": "legacy::fpu", "section_name": "FPU",
    }]}
    rtl = {"modules": [{
        "id": "legacy::module::fpu", "name": "fpu", "file_path": "rtl/fpu.sv",
        "declarations": [{
            "symbol_id": "legacy::rtl::rm", "symbol_name": "rm_i",
            "bit_width": 3, "dir": "input", "line": 9,
        }],
    }]}
    hierarchy = {"nodes": [{"node_id": "legacy::fpu", "title": "FPU", "type": "section"}]}

    artifact = bridge.build_bridge(specs, rtl, hierarchy)

    assert artifact["signal_alignments"][0]["rtl_signal_id"] == "legacy::rtl::rm"
    assert artifact["signal_alignments"][0]["spec_signal_id"] == "legacy::rm"
    assert artifact["module_alignments"][0]["rtl_module_id"] == "legacy::module::fpu"


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
        {"node_id": "sec::root", "section_id": "sec::root",
         "section_title": "Floating Point Unit", "section_name": "Floating Point Unit"},
        {"node_id": "sec::round", "section_id": "sec::root",
         "section_title": "Floating Point Unit", "subsection_id": "sec::round",
         "subsection_title": "Rounding Control", "section_name": "Rounding Control"},
    ]}]}

    sections = [bridge._canonical_section(row) for row in bridge._section_rows(hierarchy)]

    assert [(section["section_id"], section["title"]) for section in sections] == [
        ("sec::root", "Floating Point Unit"),
        ("sec::round", "Rounding Control"),
    ]


def test_observed_usage_separates_a_driver_from_identically_named_passthrough_ports():
    """Prefer the module the compiler shows driving a signal over an idle duplicate."""
    spec = bridge.spec_signals(_spec())[0]
    site = {"file": "rtl/fpu.sv", "line": 19}
    driver = _rtl_signal("rounding_mode", "rtl::signal::driver")
    driver["reader_sites"] = [site, site, site]
    driver["writer_sites"] = [site]
    idle = _rtl_signal("rounding_mode", "rtl::signal::idle")
    idle["reader_sites"] = [site]
    rtl = _rtl(
        ("idle", _module("idle", "rtl::module::idle", [idle], original_name="fpu")),
        ("driver", _module("driver", "rtl::module::driver", [driver],
                           original_name="fpu")),
    )

    outcome, record = bridge.align_signal(spec, bridge.rtl_signals(rtl))

    assert outcome == "aligned"
    assert record["rtl_signal_id"] == "rtl::signal::driver"


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


def test_threaded_ports_collapse_to_the_site_that_drives_the_net():
    def candidate(module, writers, rank):
        return {"candidate_rank": rank, "rtl": {
            "net_id": "bridge::net::one", "module_name": module,
            "signal_id": f"rtl::port::{module}::MAWBurst",
            "writer_sites": [{"line": 1}] * writers, "reader_sites": []}}

    close = [candidate("fetch_stage", 0, 1), candidate("cache", 2, 2)]

    assert [item["rtl"]["module_name"]
            for item in bridge._collapse_threaded_ports(close)] == ["cache"]


def test_candidates_on_different_nets_stay_ambiguous():
    def candidate(net, module):
        return {"candidate_rank": 1, "rtl": {
            "net_id": net, "module_name": module, "writer_sites": [], "reader_sites": []}}

    close = [candidate("bridge::net::one", "cache"), candidate("bridge::net::two", "risc_mem")]

    assert bridge._collapse_threaded_ports(close) == close



def test_the_bridge_has_no_tiny_model_resolver_or_lookup_helpers():
    assert not any(hasattr(bridge, name) for name in (
        "ollama_resolver", "ambiguity_evidence", "lookup_signal", "lookup_module"))
    with pytest.raises(SystemExit):
        bridge._arguments(["--llm"])
