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


def _table(doc, label, rows):
    return {"document_id": doc, "chunk_type": "table", "context_header": "h", "text": "t",
            "table_id": f"{doc}::table::{label}", "table_label": label, "table_rows": rows}


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
