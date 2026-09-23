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
    metrics = chunk_metrics(records, _token_counter())
    metrics["ingest.chunks_bytes"] = cfg.CHUNKS_PATH.stat().st_size
    with open(cfg.SPEC_HIERARCHY_PATH, encoding="utf-8") as handle:
        for document in json.load(handle)["documents"]:
            metrics[f"ingest.{document['document_id']}.sections"] = len(document["sections"])
            for key in ("furniture_blocks_stripped", "furniture_text_only_hits"):
                metrics[f"ingest.{document['document_id']}.{key}"] = document[key]
    with open(cfg.SPEC_SIGNALS_PATH, encoding="utf-8") as handle:
        signals = json.load(handle)["signals"]
    with open(AXI_CHANNEL_GOLD, encoding="utf-8") as handle:
        metrics.update(signal_metrics(signals, json.load(handle)["names"]))
    with open(cfg.RTL_CHUNKS_PATH, encoding="utf-8") as handle:
        metrics["rtl.chunks"] = sum(1 for _ in handle)
    with open(cfg.BRIDGE_PATH, encoding="utf-8") as handle:
        bridge = json.load(handle)
    metrics.update({f"bridge.{key}": len(value) for key, value in bridge.items()
                    if isinstance(value, list)})
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
