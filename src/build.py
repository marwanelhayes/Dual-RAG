"""Rebuild the complete Dual-RAG corpus from deterministic source artifacts.

Flow: Create a staging generation -> run ordered stages -> validate source/index integrity -> publish on success.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
import uuid

import configure as cfg


OBSOLETE_ARTIFACTS = (
    "graph.json",
    "kg_raw.jsonl",
    "kg_typed.jsonl",
    "kg_manifest.json",
    "kg_classify_cache.json",
    "kg_graph.json",
    "lexicon.json",
    "chroma_graph",
)


def _validated_output_dir():
    """Return the generated-data directory after a strict scope check.

    Returns:
        Resolved output directory inside the project root.

    Raises:
        RuntimeError: If configuration points outside the project or at its root.
    """
    root = Path(cfg.ROOT).resolve()
    output = Path(cfg.OUTPUT_DIR).resolve()
    if output == root or root not in output.parents:
        raise RuntimeError(f"refusing unsafe generated-data path: {output}")
    return output


def remove_obsolete_artifacts():
    """Delete only named artifacts belonging to the retired graph architecture.

    Returns:
        Removed artifact paths.
    """
    output = _validated_output_dir()
    removed = []
    for name in OBSOLETE_ARTIFACTS:
        target = output / name
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(target)
        elif target.exists():
            target.unlink()
            removed.append(target)
    return removed


def clean_generated_data():
    """Remove and recreate the project-scoped generated-data directory.

    Returns:
        Recreated output directory.
    """
    output = _validated_output_dir()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    return output


def stage_scripts(include_embeddings=True):
    """Return rebuild scripts in dependency order.

    Args:
        include_embeddings: Include the specification vector-index stage.

    Returns:
        Stage label and script-path pairs.
    """
    source = Path(__file__).resolve().parent
    stages = (("spec ingestion", "ingest.py"), ("spec signals", "spec_signals.py"),
              ("spec index", "embed.py"), ("RTL ingestion", "rtl_ingest.py"),
              ("RTL records", "rtl_embed.py"), ("cross-domain bridge", "bridge.py"))
    return [(label, source / name) for label, name in stages
            if include_embeddings or name != "embed.py"]


def stage_command(script):
    """Return one isolated stage command.

    Args:
        script: Stage script path.

    Returns:
        Python subprocess argument list.
    """
    return [sys.executable, "-B", str(script)]


def run_pipeline(include_embeddings=True, environment=None):
    """Run every selected stage as an isolated Python process.

    Args:
        include_embeddings: Build the vector indexes when true.

    Returns:
        None.
    """
    if environment is None:
        remove_obsolete_artifacts()
    for label, script in stage_scripts(include_embeddings=include_embeddings):
        print(f"====  {label}: {script.name}", flush=True)
        subprocess.run(stage_command(script), cwd=cfg.ROOT, check=True,
                       env=environment or os.environ.copy())


def build_generation(include_embeddings=True):
    """Validate a complete isolated rebuild before atomically changing the active generation."""
    import artifacts
    import json

    root = cfg.OUTPUT_ROOT
    if root == cfg.ROOT or not root.is_relative_to(cfg.ROOT):
        raise RuntimeError("generated data must be inside the project root")
    directory = root / "generations" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    environment = {**os.environ, "CHIPVER_OUTPUT_DIR": str(directory),
                   "CHIPVER_BUILDING_GENERATION": "1"}
    inputs = artifacts.source_hashes()
    metadata = artifacts.build_metadata()
    run_pipeline(include_embeddings, environment)
    if artifacts.source_hashes() != inputs:
        raise RuntimeError("sources changed during the build; generation was not published")
    required = artifacts.ARTIFACT_NAMES if include_embeddings else artifacts.ARTIFACT_NAMES[:-1]
    for name in required:
        path = directory / name
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"generation missing {name}; previous corpus remains active")
    chunks = [json.loads(line) for line in (directory / "chunks.jsonl").read_text().splitlines()]
    if include_embeddings:
        from embed import index_manifest_status

        status = index_manifest_status(chunks, directory / "spec_index",
                                       directory / "embed_manifest.json")
        if not status["fresh"]:
            raise RuntimeError(f"generation index invalid: {status['reason']}")
    artifacts.write_generation_manifest(directory, inputs, metadata)
    artifacts.verify_generation(directory)
    if include_embeddings:
        artifacts.publish_generation(root, directory)
        print(f"published generation {directory}")
    else:
        print(f"parser-only generation {directory}; active serving corpus unchanged")
    return directory


def main(argv=None):
    """Parse rebuild options and run the complete deterministic pipeline.

    Args:
        argv: Optional argument list for tests.

    Returns:
        None.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true",
                        help="build a fresh generation (always enabled; preserves the active corpus)")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="skip the spec vector index for a parser/bridge-only rebuild")
    args = parser.parse_args(argv)
    build_generation(include_embeddings=not args.no_embeddings)


if __name__ == "__main__":
    main()
