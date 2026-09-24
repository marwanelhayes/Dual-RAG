"""RTL exact-first retrieval over records built by ``rtl_embed``."""

from langchain_core.documents import Document

import retrieve
import rtl_embed as re_
from test_rtl_embed import _declaration, _module, _rtl


def test_exact_signal_lookup_returns_owning_module_metadata_not_a_signal_vector(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    hit = retrieve.exact_lookup("What drives FPUValidD?", records, k=1)[0]

    assert hit.metadata["source_type"] == "module"
    assert hit.metadata["module"] == "fpu_decoder"
    assert hit.metadata["exact_match_type"] == "signal"
    assert hit.metadata["matched_signal"]["signal_id"] == "rtl::sig::fpu_decoder::FPUValidD"
    assert hit.metadata["roles"] == ["driver"]


def test_exact_lookup_resolves_a_module_named_in_expanded_words(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    hit = retrieve.exact_lookup("what does the floating point unit decoder do?", records, k=1)[0]

    assert hit.metadata["module"] == "fpu_decoder"
    assert "exact_match_type" not in hit.metadata


def test_exact_lookup_prefers_the_module_the_query_named_over_a_shared_signal(tmp_path):
    named = tmp_path / "risc_fpu.sv"
    named.write_text("module risc_fpu; endmodule\n")
    other = tmp_path / "flp_add_sub.sv"
    other.write_text("module flp_add_sub; endmodule\n")
    rtl = {"modules": {}, "instance_tree": []}
    for source, module in ((named, "risc_fpu"), (other, "flp_add_sub")):
        rtl["modules"][module] = _module(module, source, parameters=[
            {"signal_id": f"rtl::par::{module}::PRECISION",
             "construct_type": "PARAMETER", "original_name": "PRECISION"}])
    records = re_.retrieval_records(rtl)

    ranked = [hit.metadata["module"]
              for hit in retrieve.exact_lookup("which module instantiates risc_fpu "
                                          "and with what PRECISION parameter?", records)]

    assert ranked[0] == "risc_fpu"


def test_exact_lookup_ignores_plain_words_that_collide_with_a_signal_name(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    assert retrieve.exact_lookup("which module can enable this?", records) == []


def test_exact_signal_search_fills_with_ast_blocks_using_that_signal(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    hits = retrieve.search("Where is FPUValidD written?", k=2, records=records)

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

    hits = retrieve.search("Where is FPUValidD written?", k=3,
                      records=re_.retrieval_records(rtl))

    assert hits[-1].metadata["module"] == "fpu_decoder"
    assert "FPUValidD" in hits[-1].metadata["signals_written"]


def test_exact_lookup_runs_before_loading_the_dense_model(monkeypatch, tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    monkeypatch.setattr(retrieve.VectorStore, "load",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("dense store loaded")))
    monkeypatch.setattr(retrieve, "Embedder",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("embedder loaded")))

    hit = retrieve.search("FPUValidD", k=1, records=records)[0]

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

    hits = retrieve.search("FPUValidD behavior", k=2, records=records, store=FakeStore())

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

    monkeypatch.setattr(retrieve.Retriever, "load",
                        lambda records, index_dir: FakeHybrid())

    hits = retrieve.search("pipeline forwarding behavior", k=1, records=records)

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

    hits = retrieve.RTLRetriever(records).search("Where is SharedSignal written?", top_k=4)

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
    module_doc = retrieve.record_to_document(module)

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

    hits = retrieve.RTLRetriever([module, block], hybrid=Hybrid()).search(
        "where does the pipeline stall", top_k=2)

    assert [hit.metadata["chunk_id"] for hit in hits] == [
        "rtl:construct:hazard_unit:always:1-2", "rtl:hazard_unit"]


def test_generic_short_words_do_not_activate_signal_lookup(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    rtl = _rtl(source)
    rtl["modules"]["fpu_decoder"]["signals"].append(
        _declaration("fpu_decoder", "in"))

    assert retrieve.exact_lookup("What is in this module?", re_.retrieval_records(rtl)) == []

