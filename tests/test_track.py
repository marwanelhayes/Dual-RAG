"""Hermetic tests for the MLflow run-summary helpers."""

import types

import track


def test_config_params_keep_only_uppercase_scalars():
    module = types.SimpleNamespace(
        MAX_CHUNK_CHARS=800, EMBED_MODEL="bge", EMBED_NORMALIZE=True,
        SKIP_TITLES={"index"}, lower_case=1)

    assert track.config_params(module) == {
        "MAX_CHUNK_CHARS": 800, "EMBED_MODEL": "bge", "EMBED_NORMALIZE": True}


def test_flatten_keeps_numeric_leaves_under_dotted_names():
    nested = {"routing_accuracy": 1.0, "spec": {"mrr": 0.5, "hit_distribution": {"0 of 3": 2}},
              "label": "x", "ok": True}

    assert track.flatten("retrieval", nested) == {
        "retrieval.routing_accuracy": 1.0, "retrieval.spec.mrr": 0.5,
        "retrieval.spec.hit_distribution.0 of 3": 2, "retrieval.ok": 1}


def _prose(doc, text, header="h"):
    return {"document_id": doc, "chunk_type": "prose", "context_header": header, "text": text}


def _table(doc, label, rows, columns=()):
    return {"document_id": doc, "chunk_type": "table", "context_header": "h", "text": "t",
            "table_id": f"{doc}::table::{label}", "table_label": label, "table_rows": rows,
            "columns": list(columns)}


def test_chunk_metrics_count_tokens_tables_and_leaks():
    records = [
        _prose("axi", "see Table A2.2 and Table 9"),
        _prose("axi", "12.1. Supervisor CSRs | Page 116 body"),
        _table("axi", "A2.2", 3),
        _table("axi", "A2.2", 3),
    ]
    count_tokens = lambda text: len(text.split())

    metrics = track.chunk_metrics(records, count_tokens, limit=8)

    assert metrics["ingest.chunks"] == 4
    assert metrics["ingest.axi.chunks"] == 4
    assert metrics["ingest.axi.tables_found"] == 1
    assert metrics["ingest.table_rows_total"] == 3
    assert metrics["ingest.table_refs_unresolved"] == 1
    assert metrics["ingest.header_leaks"] == 1
    assert metrics["ingest.prose.chunk_tokens_max"] == 8
    assert metrics["ingest.chunks_over_limit"] == 0


def _signal(doc, table, name, width=None, source=None, description=None):
    return {"document_id": doc, "table_id": f"{doc}::table::{table}",
            "normalized_name": name, "width": width, "source_role_source": source,
            "description": description}


def test_signal_metrics_count_mentions_names_fills_and_gold_recall():
    signals = [
        _signal("axi", "B1.1", "awvalid", width=1, source="Manager", description="valid"),
        _signal("axi", "A2.2", "awvalid"),
        _signal("axi", "B1.1", "awready", width=1),
        _signal("riscv", "7", "mstatus", description="status"),
    ]

    metrics = track.signal_metrics(signals, gold_names=["awvalid", "awready", "wvalid", "bready"])

    assert metrics["signals.rows"] == 4
    assert metrics["signals.unique_names"] == 3
    assert metrics["signals.axi.rows"] == 3
    assert metrics["signals.axi.unique_names"] == 2
    assert metrics["signals.tables_admitted"] == 3
    assert metrics["signals.with_width"] == 2
    assert metrics["signals.with_source"] == 1
    assert metrics["signals.with_description"] == 2
    assert metrics["signals.axi_channel_recall"] == 0.5


def _declaration(kind, module, name, width=None, writers=()):
    prefix = {"PORT": "port", "SIGNAL": "sig", "PARAMETER": "param"}[kind]
    return {"signal_id": f"rtl::{prefix}::{module}::{name}", "construct_type": kind,
            "width": width, "writer_sites": list(writers)}


def test_rtl_metrics_count_records_and_rate_resolution():
    child = {"ports": [_declaration("PORT", "child", "a", 1), _declaration("PORT", "child", "b")],
             "signals": [], "parameters": [_declaration("PARAMETER", "child", "N", 32)],
             "instances": [], "always_blocks": [], "functions_tasks": [],
             "assignment_groups": [], "other_blocks": []}
    top = {"ports": [_declaration("PORT", "top", "x", 1)],
           "signals": [_declaration("SIGNAL", "top", "s", 1, writers=[{}]),
                       _declaration("SIGNAL", "top", "t", 1)],
           "parameters": [],
           "instances": [{"bindings": [
               {"formal_signal_id": "rtl::port::child::a", "actual_signals": ["s"],
                "actual_signal_ids": ["rtl::sig::top::s"]},
               {"formal_signal_id": "rtl::port::child::zz", "actual_signals": ["t", "u"],
                "actual_signal_ids": ["rtl::sig::top::t"]}]}],
           "always_blocks": [{"construct_type": "ALWAYS_FF"}], "functions_tasks": [],
           "assignment_groups": [{"construct_type": "ASSIGNMENT_GROUP"}], "other_blocks": []}
    rtl = {"modules": {"child": child, "top": top}, "tree": [{}, {}],
           "enums": [{"items": [{}, {}]}],
           "warnings": [{"code": "WIDTHEXPAND"}, {"code": "WIDTHEXPAND"}]}

    metrics = track.rtl_metrics(rtl)

    assert metrics["rtl.modules"] == 2
    assert metrics["rtl.ports"] == 3
    assert metrics["rtl.signals"] == 2
    assert metrics["rtl.parameters"] == 1
    assert metrics["rtl.instances"] == 1
    assert metrics["rtl.bindings"] == 2
    assert metrics["rtl.blocks.ALWAYS_FF"] == 1
    assert metrics["rtl.blocks.ASSIGNMENT_GROUP"] == 1
    assert metrics["rtl.enums"] == 1
    assert metrics["rtl.enum_items"] == 2
    assert "rtl.enum_usage_sites" not in metrics
    assert metrics["rtl.tree_nodes"] == 2
    assert metrics["rtl.warnings.WIDTHEXPAND"] == 2
    assert metrics["rtl.declarations_with_width_rate"] == 5 / 6
    assert metrics["rtl.bindings_formal_resolved_rate"] == 0.5
    assert metrics["rtl.bindings_actuals_resolved_rate"] == 0.5
    assert metrics["rtl.signals_with_writer_rate"] == 0.5


def test_rtl_chunk_metrics_count_records_line_coverage_and_over_limit():
    block = {"source_code": "always_comb\n  a = b;\n\n  c = d;\n"}
    rtl = {"modules": {"m": {"always_blocks": [block], "functions_tasks": [],
                             "assignment_groups": []}}}
    records = [
        {"source_type": "module", "module": "m", "context_header": "RTL > m",
         "text": "module m\nports: a, b, c, d, e, f"},
        {"source_type": "construct", "module": "m", "context_header": "RTL > m",
         "text": "reads: b\nalways_comb\n  a = b;"},
    ]
    count_tokens = lambda text: len(text.split())

    metrics = track.rtl_chunk_metrics(records, rtl, count_tokens, limit=10)

    assert metrics["rtl.records.module"] == 1
    assert metrics["rtl.records.construct"] == 1
    # Two of the block's three non-blank source lines reach the embedder.
    assert metrics["rtl.embedded_line_coverage"] == 2 / 3
    assert metrics["rtl.chunks_over_limit"] == 1
    assert metrics["rtl.construct.chunk_tokens_max"] == 9


def test_prose_table_overlap_counts_prose_repeating_a_table_header():
    records = [
        _prose("axi", "Signal Source Description AWID Manager Write id"),
        _prose("axi", "Ordinary prose about Signal and Source."),
        _prose("riscv", "Signal Source Description"),
        _table("axi", "A2.1", 2, columns=["Signal", "Source", "Description"]),
    ]

    metrics = track.chunk_metrics(records, lambda text: 1)

    assert metrics["ingest.prose_table_overlap"] == 1


def test_bridge_metrics_score_axi_links_against_the_gold_ports():
    gold = [{"spec_name": "AWVALID", "rtl_port": "MAWValid"},
            {"spec_name": "RDATA", "rtl_port": "MRRData"}]
    link = lambda name, port, doc="axi_spec", method="identity": {
        "spec_signal_id": f"spec::signal::{doc}::A1::{name.lower()}",
        "spec_original_name": name, "rtl_original_name": port, "method": method}
    bridge = {
        "signal_alignments": [link("AWVALID", "MAWValid"), link("AWVALID", "DMAWValid"),
                              link("AWNSAID", "MAWValid", method="interface_prefix"),
                              link("mstatus", "mstatus", doc="riscv_privileged")],
        "unresolved_signals": [{"reason": "no link"}, {"reason": "no link"}],
        "module_alignments": [{"rtl_module_id": "m"}],
    }

    metrics = track.bridge_metrics(bridge, gold)

    assert metrics["bridge.axi_recall"] == 0.5
    assert metrics["bridge.axi_precision"] == 2 / 3
    assert metrics["bridge.links.identity"] == 3
    assert metrics["bridge.links.interface_prefix"] == 1
    assert metrics["bridge.links_to.signal"] == 4
    assert metrics["bridge.unresolved.no link"] == 2
