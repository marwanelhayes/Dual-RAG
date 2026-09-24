"""Log one pipeline run to MLflow: configuration, stage metrics, and key artifacts.

Run after ``python src/build.py`` and ``python src/bench.py --retrieval``:
``python src/track.py --tag v0-baseline``. View with
``mlflow ui --backend-store-uri sqlite:///mlflow.db``.
"""

import argparse
import collections
import json
import re
import subprocess

import configure as cfg

TRACKING_URI = f"sqlite:///{cfg.ROOT / 'mlflow.db'}"
EXPERIMENT = "chipver"
TOKEN_LIMIT = 512
CHUNK_SAMPLE_LINES = 200
# AXI Tables B1.1-B1.7 read from the PDF, independent of ingest.
AXI_CHANNEL_GOLD = cfg.ROOT / "eval" / "axi_channel_signals.json"
# The AXI channel signals the RTL implements, with their RTL ports, read from rtl/*.sv.
BRIDGE_GOLD = cfg.ROOT / "eval" / "bridge_gold.json"

_TABLE_REF = re.compile(r"\bTable\s+([A-Z]?\d+(?:[.-]\d+)*)")
_HEADER_LEAK = re.compile(r"\| Page \d+|Chapter [A-Z]\d+\.")


def config_params(module):
    """Return a configuration module's uppercase scalar constants as MLflow params.

    Args:
        module: The ``configure`` module or any object with attributes.

    Returns:
        Constant name to bool, int, float, or str value; containers and paths are skipped.
    """
    return {name: value for name, value in vars(module).items()
            if name.isupper() and isinstance(value, (bool, int, float, str))}


def flatten(prefix, mapping):
    """Flatten nested numeric report values into dotted MLflow metric names.

    Args:
        prefix: Name prefix for every metric.
        mapping: Nested dict of report values.

    Returns:
        Dotted metric name to float; non-numeric leaves are dropped.
    """
    flat = {}
    for key, value in mapping.items():
        name = f"{prefix}.{key}"
        if isinstance(value, dict):
            flat.update(flatten(name, value))
        elif isinstance(value, (bool, int, float)):
            flat[name] = float(value)
    return flat


def _percentiles(prefix, values):
    """Summarize token counts as p50, p95, and max.

    Args:
        prefix: Metric name prefix.
        values: Token counts.

    Returns:
        Three metrics, or none when there are no values.
    """
    if not values:
        return {}
    ordered = sorted(values)
    return {f"{prefix}_p50": ordered[len(ordered) // 2],
            f"{prefix}_p95": ordered[int(len(ordered) * 0.95)],
            f"{prefix}_max": ordered[-1]}


def chunk_metrics(records, count_tokens, limit=TOKEN_LIMIT):
    """Measure spec chunks intrinsically, without any retrieval.

    Args:
        records: Chunk dicts from ``chunks.jsonl``.
        count_tokens: Callable returning the encoder token count of one string.
        limit: Encoder window; embedded text over it is silently truncated.

    Returns:
        Metric name to value, prefixed ``ingest.``.
    """
    metrics = collections.Counter({"ingest.chunks": len(records)})
    tokens = collections.defaultdict(list)
    table_rows, labels = {}, collections.defaultdict(set)
    for record in records:
        doc = record["document_id"]
        metrics[f"ingest.{doc}.chunks"] += 1
        # Count exactly what embed.py encodes: header, blank line, body.
        tokens[record["chunk_type"]].append(
            count_tokens(record["context_header"] + "\n\n" + record["text"]))
        if record["chunk_type"] == "table":
            table_rows[record["table_id"]] = record["table_rows"]
            labels[doc].add(record["table_label"])
    for doc, found in labels.items():
        metrics[f"ingest.{doc}.tables_found"] = len(found)
    prose = [record for record in records if record["chunk_type"] == "prose"]
    metrics["ingest.table_rows_total"] = sum(table_rows.values())
    metrics["ingest.table_refs_unresolved"] = sum(
        label not in labels[record["document_id"]]
        for record in prose for label in dict.fromkeys(_TABLE_REF.findall(record["text"])))
    metrics["ingest.header_leaks"] = sum(bool(_HEADER_LEAK.search(r["text"])) for r in prose)
    headers = collections.defaultdict(set)
    for record in records:
        columns = [cell for cell in record.get("columns", []) if cell.strip()]
        if record["chunk_type"] == "table" and len(columns) >= 2:
            headers[record["document_id"]].add(" ".join(" ".join(columns).split()))
    metrics["ingest.prose_table_overlap"] = sum(
        any(header in " ".join(record["text"].split())
            for header in headers[record["document_id"]]) for record in prose)
    metrics["ingest.chunks_over_limit"] = sum(
        count > limit for counts in tokens.values() for count in counts)
    for chunk_type, counts in tokens.items():
        metrics.update(_percentiles(f"ingest.{chunk_type}.chunk_tokens", counts))
    return dict(metrics)


def signal_metrics(signals, gold_names):
    """Measure the spec signal inventory intrinsically, without linking.

    Args:
        signals: Records from ``spec_signals.json``; one record is one table-row mention.
        gold_names: Frozen normalized names every run must still extract.

    Returns:
        Metric name to value, prefixed ``signals.``.
    """
    metrics = collections.Counter({"signals.rows": len(signals)})
    names = collections.defaultdict(set)
    for signal in signals:
        doc = signal["document_id"]
        metrics[f"signals.{doc}.rows"] += 1
        names[doc].add(signal["normalized_name"])
    for doc, found in names.items():
        metrics[f"signals.{doc}.unique_names"] = len(found)
    metrics["signals.unique_names"] = sum(len(found) for found in names.values())
    metrics["signals.tables_admitted"] = len({signal["table_id"] for signal in signals})
    # source_role_source is the raw Source cell until consolidation renames it source.
    for metric, key in (("with_width", "width"), ("with_source", "source_role_source"),
                        ("with_description", "description")):
        metrics[f"signals.{metric}"] = sum(signal.get(key) is not None for signal in signals)
    extracted = set().union(*names.values())
    metrics["signals.axi_channel_recall"] = (
        sum(name in extracted for name in gold_names) / len(gold_names))
    return dict(metrics)


def rtl_metrics(rtl):
    """Measure the RTL artifact intrinsically: record counts and resolution rates.

    Args:
        rtl: Parsed ``rtl.json`` payload.

    Returns:
        Metric name to value, prefixed ``rtl.``; a falling rate means lost structure.
    """
    modules = list(rtl["modules"].values())
    declarations = [item for module in modules
                    for key in ("ports", "signals", "parameters") for item in module[key]]
    ports = {item["signal_id"] for module in modules for item in module["ports"]}
    declared = {item["signal_id"] for item in declarations}
    bindings = [binding for module in modules for instance in module["instances"]
                for binding in instance["bindings"]]
    signals = [item for module in modules for item in module["signals"]]
    items = [item for enum in rtl["enums"] for item in enum["items"]]
    metrics = collections.Counter({
        "rtl.modules": len(modules),
        "rtl.ports": len(ports),
        "rtl.signals": len(signals),
        "rtl.parameters": sum(len(module["parameters"]) for module in modules),
        "rtl.instances": sum(len(module["instances"]) for module in modules),
        "rtl.bindings": len(bindings),
        "rtl.enums": len(rtl["enums"]),
        "rtl.enum_items": len(items),
        "rtl.enum_usage_sites": sum(len(item.get("usage_sites", [])) for item in items),
        "rtl.tree_nodes": len(rtl["tree"]),
    })
    for module in modules:
        for key in ("always_blocks", "functions_tasks", "assignment_groups", "other_blocks"):
            for block in module[key]:
                metrics[f"rtl.blocks.{block['construct_type']}"] += 1
    for warning in rtl["warnings"]:
        metrics[f"rtl.warnings.{warning['code']}"] += 1
    rate = lambda hits, total: hits / total if total else 0.0
    metrics["rtl.declarations_with_width_rate"] = rate(
        sum(item.get("width") is not None for item in declarations), len(declarations))
    metrics["rtl.bindings_formal_resolved_rate"] = rate(
        sum(binding["formal_signal_id"] in ports for binding in bindings), len(bindings))
    metrics["rtl.bindings_actuals_resolved_rate"] = rate(
        sum(len(binding["actual_signal_ids"]) == len(binding["actual_signals"])
            and set(binding["actual_signal_ids"]) <= declared for binding in bindings),
        len(bindings))
    metrics["rtl.signals_with_writer_rate"] = rate(
        sum(bool(item["writer_sites"]) for item in signals), len(signals))
    return dict(metrics)


def rtl_chunk_metrics(records, rtl, count_tokens, limit=TOKEN_LIMIT):
    """Measure RTL retrieval records against the block source they should carry.

    Args:
        records: Records from ``rtl_chunks.jsonl``.
        rtl: Parsed ``rtl.json`` payload.
        count_tokens: Callable returning the encoder token count of one string.
        limit: Encoder window; embedded text over it is silently truncated.

    Returns:
        Record counts per type, block-line coverage, token percentiles, over-limit count.
    """
    metrics = collections.Counter()
    tokens = collections.defaultdict(list)
    embedded = collections.defaultdict(set)
    for record in records:
        metrics[f"rtl.records.{record['source_type']}"] += 1
        tokens[record["source_type"]].append(
            count_tokens(record["context_header"] + "\n\n" + record["text"]))
        if record["source_type"] == "construct":
            embedded[record["module"]].update(
                line.strip() for line in record["text"].splitlines())
    total = covered = 0
    for name, module in rtl["modules"].items():
        for field in ("always_blocks", "functions_tasks", "assignment_groups"):
            for block in module[field]:
                lines = [line.strip() for line in block["source_code"].splitlines()
                         if line.strip()]
                total += len(lines)
                covered += sum(line in embedded[name] for line in lines)
    metrics["rtl.embedded_line_coverage"] = covered / total if total else 0.0
    metrics["rtl.chunks_over_limit"] = sum(
        count > limit for counts in tokens.values() for count in counts)
    for source_type, counts in tokens.items():
        metrics.update(_percentiles(f"rtl.{source_type}.chunk_tokens", counts))
    return dict(metrics)


def bridge_metrics(bridge, gold):
    """Score AXI spec-to-RTL links against the gold ports and count the rest.

    Args:
        bridge: Decoded ``bridge.json``.
        gold: ``{spec_name, rtl_port}`` rows; a port's ``D``/``I`` aliases also count.

    Returns:
        Recall (gold names linked to their port), precision (AXI link rows that are
        correct), links per method, unresolved per reason, and module alignments.
    """
    ports = {row["spec_name"]: {row["rtl_port"], "D" + row["rtl_port"], "I" + row["rtl_port"]}
             for row in gold}
    links = bridge.get("signal_alignments", [])
    axi = [link for link in links if "::axi_spec::" in link["spec_signal_id"]]
    correct = [link for link in axi
               if link["rtl_original_name"] in ports.get(link["spec_original_name"], ())]
    metrics = collections.Counter({
        "bridge.axi_recall": len({link["spec_original_name"] for link in correct}) / len(ports),
        "bridge.axi_precision": len(correct) / len(axi) if axi else 0.0,
        "bridge.signal_alignments": len(links),
        "bridge.unresolved_signals": len(bridge.get("unresolved_signals", [])),
        "bridge.module_alignments": len(bridge.get("module_alignments", [])),
    })
    for link in links:
        metrics[f"bridge.links.{link.get('method', 'scored')}"] += 1
    for row in bridge.get("unresolved_signals", []):
        metrics[f"bridge.unresolved.{row['reason']}"] += 1
    return dict(metrics)


def _git(*args):
    """Return one git command's trimmed stdout, run from the project root.

    Args:
        args: git arguments.

    Returns:
        Command output.
    """
    return subprocess.run(["git", *args], cwd=cfg.ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def _token_counter():
    """Load the embedder's tokenizer as a token-counting callable.

    Returns:
        Callable counting encoder tokens, special tokens included.
    """
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(cfg.EMBED_MODEL)
    tokenizer.model_max_length = 10 ** 6  # measure past 512 instead of warning
    return lambda text: len(tokenizer(text)["input_ids"])


def pipeline_metrics():
    """Measure every stage's current output artifacts.

    Returns:
        Metric name to value across ingest, downstream stages, and retrieval.
    """
    with open(cfg.CHUNKS_PATH, encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    count_tokens = _token_counter()
    metrics = chunk_metrics(records, count_tokens)
    metrics["ingest.chunks_bytes"] = cfg.CHUNKS_PATH.stat().st_size
    with open(cfg.SPEC_HIERARCHY_PATH, encoding="utf-8") as handle:
        for document in json.load(handle)["documents"]:
            metrics[f"ingest.{document['document_id']}.sections"] = len(document["sections"])
            for key in ("furniture_blocks_stripped", "furniture_text_only_hits",
                        "fallback_heading_precision", "fallback_heading_recall",
                        "fallback_heading_f1"):
                if key in document:
                    metrics[f"ingest.{document['document_id']}.{key}"] = document[key]
    with open(cfg.SPEC_SIGNALS_PATH, encoding="utf-8") as handle:
        signals = json.load(handle)["signals"]
    with open(AXI_CHANNEL_GOLD, encoding="utf-8") as handle:
        metrics.update(signal_metrics(signals, json.load(handle)["names"]))
    with open(cfg.RTL_PATH, encoding="utf-8") as handle:
        rtl = json.load(handle)
    metrics.update(rtl_metrics(rtl))
    metrics["rtl.rtl_bytes"] = cfg.RTL_PATH.stat().st_size
    with open(cfg.RTL_CHUNKS_PATH, encoding="utf-8") as handle:
        rtl_records = [json.loads(line) for line in handle]
    metrics["rtl.chunks"] = len(rtl_records)
    metrics.update(rtl_chunk_metrics(rtl_records, rtl, count_tokens))
    with open(cfg.BRIDGE_PATH, encoding="utf-8") as handle:
        bridge = json.load(handle)
    with open(BRIDGE_GOLD, encoding="utf-8") as handle:
        metrics.update(bridge_metrics(bridge, json.load(handle)["signals"]))
    if cfg.RETRIEVAL_PATH.exists():
        with open(cfg.RETRIEVAL_PATH, encoding="utf-8") as handle:
            metrics.update(flatten("retrieval", json.load(handle)["metrics"]["overall"]))
    return metrics


def main(argv=None):
    """Log the current pipeline outputs as one MLflow run.

    Args:
        argv: Optional argument list for tests.

    Returns:
        None.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="step tag, e.g. ingest/caption-regex")
    args = parser.parse_args(argv)

    import mlflow

    metrics = pipeline_metrics()
    mlflow.set_tracking_uri(TRACKING_URI)
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=(cfg.ROOT / "mlruns").as_uri())
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=args.tag) as run:
        mlflow.set_tags({"step": args.tag, "git_sha": _git("rev-parse", "HEAD"),
                         "git_dirty": str(bool(_git("status", "--porcelain")))})
        mlflow.log_params(config_params(cfg))
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(str(cfg.ROOT / "src" / "configure.py"))
        mlflow.log_artifact(str(cfg.SPEC_HIERARCHY_PATH))
        if cfg.RETRIEVAL_PATH.exists():
            mlflow.log_artifact(str(cfg.RETRIEVAL_PATH))
        with open(cfg.CHUNKS_PATH, encoding="utf-8") as handle:
            sample = "".join(line for _, line in zip(range(CHUNK_SAMPLE_LINES), handle))
        mlflow.log_text(sample, "chunks_sample.jsonl")
    print(f"logged run {run.info.run_id} ({args.tag}): {len(metrics)} metrics")


if __name__ == "__main__":
    main()
