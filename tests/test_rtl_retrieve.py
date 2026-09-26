"""RTL exact-first retrieval over records built by ``rtl_embed``."""

from langchain_core.documents import Document

import retrieve
import rtl_embed as re_
from test_rtl_embed import _declaration, _module, _rtl



def _documents(hits):
    return [document for document, _ in hits]

def test_exact_signal_lookup_returns_owning_module_metadata_not_a_signal_vector(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))

    hit = retrieve.exact_lookup("What drives FPUValidD?", records, k=1)[0]

    assert hit.metadata["source_type"] == "module"
    assert hit.metadata["module"] == "fpu_decoder"
    assert hit.metadata["exact_match_type"] == "signal"
    assert hit.metadata["symbol_name"] == "FPUValidD"


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

    hits = _documents(retrieve.RTLRetriever(records).search(
        "Where is FPUValidD written?", top_k=2))

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

    hits = _documents(retrieve.RTLRetriever(re_.retrieval_records(rtl)).search(
        "Where is FPUValidD written?", top_k=3))

    blocks = [hit for hit in hits if hit.metadata["source_type"] == "construct"]
    assert blocks[0].metadata["module"] == "fpu_decoder"
    assert "FPUValidD" in blocks[0].metadata["signals_written"]


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

    hit = _documents(retrieve.RTLRetriever(records).search("FPUValidD", top_k=1))[0]

    assert hit.metadata["symbol_name"] == "FPUValidD"


def test_ast_signal_blocks_precede_dense_fill_after_exact(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    records = re_.retrieval_records(_rtl(source))
    dense = Document(page_content="RTL > rtl/other\n\nmodule other",
                     metadata={"chunk_id": "rtl:other", "module": "other",
                               "source_type": "module"})

    class FakeHybrid:
        def search(self, query, top_k):
            return [(dense, 0.9)]

    hits = _documents(retrieve.RTLRetriever(records, hybrid=FakeHybrid()).search(
        "FPUValidD behavior", top_k=2))

    assert hits[0].metadata["symbol_name"] == "FPUValidD"
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

    hits = _documents(retrieve.RTLRetriever(records).search(
        "pipeline forwarding behavior", top_k=1))

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
                "signal_names": ["SharedSignal"],
            },
            {
                "chunk_id": f"rtl:construct:{module}:always:1-2",
                "record_id": f"block:{module}", "context_header": f"RTL > {module} > always",
                "text": "writes SharedSignal", "source_type": "construct",
                "module": module, "lookup_terms": [], "signals_read": [],
                "signals_written": ["SharedSignal"], "start_line": 1,
            },
        ])

    hits = _documents(retrieve.RTLRetriever(records).search(
        "Where is SharedSignal written?", top_k=4))

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

    hits = _documents(retrieve.RTLRetriever([module, block], hybrid=Hybrid()).search(
        "where does the pipeline stall", top_k=2))

    assert [hit.metadata["chunk_id"] for hit in hits] == [
        "rtl:construct:hazard_unit:always:1-2", "rtl:hazard_unit"]


def test_generic_short_words_do_not_activate_signal_lookup(tmp_path):
    source = tmp_path / "fpu_decoder.sv"
    source.write_text("module fpu_decoder; endmodule\n")
    rtl = _rtl(source)
    rtl["modules"]["fpu_decoder"]["signals"].append(
        _declaration("fpu_decoder", "in"))

    assert retrieve.exact_lookup("What is in this module?", re_.retrieval_records(rtl)) == []



def test_window_hits_fold_into_one_hit_for_their_record():
    base = {"chunk_id": "rtl:m", "record_id": "rtl::mod::m", "context_header": "RTL > m",
            "text": "module m", "source_type": "module", "module": "m",
            "lookup_terms": []}
    window = {**base, "chunk_id": "rtl:m:w1", "text": "comments: stall logic"}
    other = {**base, "chunk_id": "rtl:n", "module": "n", "text": "module n"}

    class Hybrid:
        def search(self, query, top_k):
            return [(retrieve.record_to_document(record), 0.9)
                    for record in (window, base, other)]

    hits = _documents(retrieve.RTLRetriever([base, window, other], hybrid=Hybrid()).search(
        "where is the stall logic", top_k=2))

    assert [hit.metadata["chunk_id"] for hit in hits] == ["rtl:m", "rtl:n"]
    assert hits[0].page_content.endswith("comments: stall logic")


def test_exact_lookup_needs_the_name_spelled_as_the_rtl_states_it():
    records = [
        {"chunk_id": "rtl:construct:flp_mul:func:1-2", "record_id": "f",
         "context_header": "RTL > flp_mul > function", "text": "", "module": "flp_mul",
         "source_type": "construct", "lookup_terms": ["Rounding"]},
        {"chunk_id": "rtl:flp_add_sub", "record_id": "m", "context_header": "RTL > flp_add_sub",
         "text": "", "module": "flp_add_sub", "source_type": "module", "lookup_terms": [],
         "signal_names": ["mode"]},
    ]

    assert retrieve.exact_lookup("Does the RTL support the Vectored MODE for rounding?",
                                 records) == []
    assert [hit.metadata["chunk_id"] for hit in retrieve.exact_lookup(
        "What does Rounding return?", records)] == ["rtl:construct:flp_mul:func:1-2"]


def test_short_signal_names_are_exact_addressable():
    records = [{"chunk_id": "rtl:fetch_stage", "record_id": "m",
                "context_header": "RTL > fetch_stage", "text": "", "module": "fetch_stage",
                "source_type": "module", "lookup_terms": [],
                "signal_names": ["PC"]}]

    hits = retrieve.exact_lookup("How is PC updated?", records)

    assert [hit.metadata["symbol_name"] for hit in hits] == ["PC"]


def test_signal_structures_are_kept_when_exact_hits_leave_room_for_dense_fill():
    module = {"chunk_id": "rtl:decode_stage", "record_id": "m", "text": "",
              "context_header": "RTL > decode_stage", "source_type": "module",
              "module": "decode_stage", "lookup_terms": [],
              "signal_names": ["SignImmD"]}
    block = {"chunk_id": "rtl:construct:decode_stage:always:1-2", "record_id": "b",
             "text": "", "context_header": "RTL > decode_stage > always",
             "source_type": "construct", "module": "decode_stage", "lookup_terms": [],
             "signals_read": [], "signals_written": ["SignImmD"], "start_line": 1}
    other = Document(page_content="", metadata={"chunk_id": "rtl:other", "module": "other",
                                                "source_type": "module"})

    class Hybrid:
        def search(self, query, top_k):
            return [(other, 1.0)]

    hits = _documents(retrieve.RTLRetriever([module, block], hybrid=Hybrid()).search(
        "How is SignImmD generated?", top_k=5))

    assert [hit.metadata["chunk_id"] for hit in hits] == [
        "rtl:decode_stage", "rtl:construct:decode_stage:always:1-2", "rtl:other"]


def test_rtl_hits_share_the_document_score_pair_shape():
    records = [{"chunk_id": "rtl:fetch_stage", "record_id": "m",
                "context_header": "RTL > fetch_stage", "text": "", "module": "fetch_stage",
                "source_type": "module", "lookup_terms": ["fetch_stage"]}]

    hits = retrieve.RTLRetriever(records).search("What does fetch_stage do?", top_k=1)

    assert [(hit.metadata["chunk_id"], score) for hit, score in hits] == [
        ("rtl:fetch_stage", None)]
