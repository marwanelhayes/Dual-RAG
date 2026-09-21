"""Focused tests for module-first RTL retrieval records."""

import json
from pathlib import Path

from langchain_core.documents import Document

import configure as cfg
import rtl_embed as re_
from embed import build_manifest, content_hash, read_manifest, write_manifest


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


def test_exact_signal_lookup_returns_owning_module_metadata_not_a_signal_vector(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    hit = re_.exact_lookup("What drives FPUValidD?", records, k=1)[0]

    assert hit.metadata["source_type"] == "module"
    assert hit.metadata["module"] == "fpu_decoder"
    assert hit.metadata["exact_match_type"] == "signal"
    assert hit.metadata["matched_signal"]["signal_id"] == "rtl::sig::fpu_decoder::FPUValidD"
    assert hit.metadata["roles"] == ["driver"]


def test_exact_lookup_resolves_a_module_named_in_expanded_words(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    hit = re_.exact_lookup("what does the floating point unit decoder do?", records, k=1)[0]

    assert hit.metadata["module"] == "fpu_decoder"
    assert "exact_match_type" not in hit.metadata


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


def test_exact_lookup_prefers_the_module_the_query_named_over_a_shared_signal(tmp_path):
    named = tmp_path / "risc_fpu.sv"
    named.write_text("module risc_fpu; endmodule\n")
    other = tmp_path / "flp_add_sub.sv"
    other.write_text("module flp_add_sub; endmodule\n")
    rtl = {"modules": {}, "instance_tree": []}
    for source, module in ((named, "risc_fpu"), (other, "flp_add_sub")):
        rtl["modules"][module] = {
            "file": str(source), "start_line": 1, "end_line": 2, "ports": [],
            "parameters": [{"signal_id": f"rtl::par::{module}::PRECISION",
                            "construct_type": "PARAMETER", "original_name": "PRECISION"}],
            "signals": [], "always_blocks": [], "functions_tasks": [],
            "assignment_groups": [], "instances": [],
        }
    records = re_.retrieval_records(rtl)

    ranked = [hit.metadata["module"]
              for hit in re_.exact_lookup("which module instantiates risc_fpu "
                                          "and with what PRECISION parameter?", records)]

    assert ranked[0] == "risc_fpu"


def test_exact_lookup_ignores_plain_words_that_collide_with_a_signal_name(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    assert re_.exact_lookup("which module can enable this?", records) == []


def test_exact_signal_search_fills_with_ast_blocks_using_that_signal(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    hits = re_.search("Where is FPUValidD written?", k=2, records=records)

    assert hits[0].metadata["exact_match_type"] == "signal"
    assert hits[1].metadata["source_type"] == "construct"
    assert "FPUValidD" in hits[1].metadata["signals_written"]


def test_exact_signal_search_prioritizes_writer_block_across_owning_modules(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    rtl = _rtl(source)
    reader = dict(rtl["modules"]["child"])
    reader["ports"] = [_declaration("child", "FPUValidD", "PORT", "input")]
    reader["always_blocks"] = [{
        **rtl["modules"]["fpu_decoder"]["always_blocks"][0],
        "block_id": "rtl::blk::child::always::25-26", "file": str(source),
        "start_line": 25, "end_line": 26, "signals_read": ["FPUValidD"],
        "signals_written": ["other"],
    }]
    rtl["modules"]["child"] = reader

    hits = re_.search("Where is FPUValidD written?", k=3,
                      records=re_.retrieval_records(rtl))

    assert hits[-1].metadata["module"] == "fpu_decoder"
    assert "FPUValidD" in hits[-1].metadata["signals_written"]


def test_exact_lookup_runs_before_loading_the_dense_model(monkeypatch, tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    monkeypatch.setattr(re_.VectorStore, "load",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("dense store loaded")))
    monkeypatch.setattr(re_, "Embedder",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("embedder loaded")))

    hit = re_.search("FPUValidD", k=1, records=records)[0]

    assert hit.metadata["matched_signal"]["original_name"] == "FPUValidD"


def test_ast_signal_blocks_precede_dense_fill_after_exact(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    dense = Document(page_content="RTL > rtl/other\n\nmodule other",
                     metadata={"chunk_id": "rtl:other", "module": "other",
                               "source_type": "module"})

    class FakeStore:
        def search(self, query, k):
            return [(dense, 0.9)]

    hits = re_.search("FPUValidD behavior", k=2, records=records, store=FakeStore())

    assert hits[0].metadata["matched_signal"]["original_name"] == "FPUValidD"
    assert hits[1].metadata["module"] == "fpu_decoder"
    assert "FPUValidD" in hits[1].metadata["signals_written"]


def test_non_exact_fill_uses_domain_hybrid_reranking(monkeypatch, tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    document = Document(page_content="ranked block", metadata={
        "chunk_id": "rtl:ranked", "module": "fpu_decoder", "source_type": "construct",
    })
    calls = []

    class FakeHybrid:
        def search(self, query, top_k):
            calls.append((query, top_k))
            return [(document, 4.2)]

    monkeypatch.setattr(re_.HybridRetriever, "load",
                        lambda records, index_dir: FakeHybrid())

    hits = re_.search("pipeline forwarding behavior", k=1, records=records)

    assert calls == [("pipeline forwarding behavior", 1)]
    assert hits == [document]


def test_exact_signal_search_splits_module_and_construct_budget():
    records = []
    for module in ("a", "b", "c", "d"):
        records.extend([
            {
                "chunk_id": f"rtl:{module}", "record_id": f"module:{module}",
                "context_header": f"RTL > {module}", "text": f"module {module}",
                "source_type": "module", "module": module, "lookup_terms": [],
                "signal_metadata": [{
                    "signal_id": f"signal:{module}", "original_name": "SharedSignal",
                    "normalized_name": "shared_signal", "writer_sites": [{}],
                    "reader_sites": [],
                }],
            },
            {
                "chunk_id": f"rtl:construct:{module}:always:1-2",
                "record_id": f"block:{module}", "context_header": f"RTL > {module} > always",
                "text": "writes SharedSignal", "source_type": "construct",
                "module": module, "lookup_terms": [], "signals_read": [],
                "signals_written": ["SharedSignal"], "start_line": 1,
            },
        ])

    hits = re_.RTLRetriever(records).search("Where is SharedSignal written?", top_k=4)

    assert [hit.metadata["source_type"] for hit in hits] == [
        "module", "construct", "module", "construct"]


def test_semantic_module_hit_reranks_its_constructs():
    module = {
        "chunk_id": "rtl:hazard_unit", "record_id": "module", "source_id": "file",
        "context_header": "RTL > hazard_unit", "text": "module hazard_unit",
        "source_type": "module", "module": "hazard_unit", "lookup_terms": [],
    }
    block = {
        "chunk_id": "rtl:construct:hazard_unit:always:1-2", "record_id": "block",
        "source_id": "file", "context_header": "RTL > hazard_unit > always",
        "text": "writes StallD", "source_type": "construct", "module": "hazard_unit",
        "lookup_terms": [],
    }
    module_doc = re_.record_to_document(module)

    class Ranker:
        def rank(self, query, documents):
            del query
            return [(document, int(document.metadata["source_type"] == "construct"))
                    for document in reversed(list(documents))]

    class Hybrid:
        reranker = Ranker()

        def search(self, query, top_k):
            del query, top_k
            return [(module_doc, 0.0)]

    hits = re_.RTLRetriever([module, block], hybrid=Hybrid()).search(
        "where does the pipeline stall", top_k=2)

    assert [hit.metadata["chunk_id"] for hit in hits] == [
        "rtl:construct:hazard_unit:always:1-2", "rtl:hazard_unit"]


def test_generic_short_words_do_not_activate_signal_lookup(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    rtl = _rtl(source)
    rtl["modules"]["fpu_decoder"]["signals"].append(
        _declaration("fpu_decoder", "in"))

    assert re_.exact_lookup("What is in this module?", re_.retrieval_records(rtl)) == []


def test_manifest_detects_missing_fresh_and_changed_surfaces(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    index_dir = tmp_path / "index"
    manifest_path = tmp_path / "manifest.json"
    index_dir.mkdir()

    assert re_.index_manifest_status(records, index_dir, manifest_path)["reason"] == "manifest missing"
    write_manifest(build_manifest(records), manifest_path)
    assert re_.index_manifest_status(records, index_dir, manifest_path)["fresh"] is True
    changed = [{**records[0], "text": records[0]["text"] + " changed"}, *records[1:]]
    status = re_.index_manifest_status(changed, index_dir, manifest_path)
    assert status["fresh"] is False
    assert status["changed_ids"] == [records[0]["chunk_id"]]


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
