"""Publish and verify immutable corpus generations with source/build provenance.

Flow: Hash sources and tools -> seal corpus JSON -> verify artifacts -> atomically publish a generation.
"""

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import configure as cfg


ARTIFACT_NAMES = ("chunks.jsonl", "spec_hierarchy.json", "spec_signals.json", "rtl.json",
                  "rtl_chunks.jsonl", "bridge.json", "embed_manifest.json")


def file_hash(path):
    """Hash a file in bounded memory."""
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_hashes():
    """Fingerprint input documents, HDL, project settings and production source code."""
    paths = [*cfg.INGEST_DIR.glob("*.pdf"),
             *(path for path in cfg.RTL_DIR.rglob("*") if path.is_file()),
             * (cfg.ROOT / "src").glob("*.py"), cfg.ROOT / "requirements.lock"]
    if cfg.RTL_PROJECT_PATH:
        paths.append(cfg.RTL_PROJECT_PATH)
    return {str(path.relative_to(cfg.ROOT)): file_hash(path) for path in sorted(set(paths))
            if path.is_file()}


def build_metadata():
    """Record exact tools, model revisions and scalar configuration for a build."""
    compiler = subprocess.run(["verilator", "--version"], capture_output=True, text=True, check=True)
    return {"python": sys.version, "verilator": compiler.stdout.strip(),
            "packages": {name: version(name) for name in
                         ("pymupdf", "torch", "transformers", "sentence-transformers", "chromadb")},
            "configuration": {name: value for name, value in vars(cfg).items()
                              if name.isupper() and isinstance(value, (str, int, float, bool))}}


def write_generation_manifest(directory, inputs, metadata):
    """Seal generated artifacts; benchmarking reports may be added without changing the seal."""
    directory = Path(directory)
    paths = [directory / name for name in ARTIFACT_NAMES if (directory / name).is_file()]
    data = {"schema_version": 1, "generation": directory.name, "inputs": inputs,
            "build": metadata, "artifacts": {str(path.relative_to(directory)): file_hash(path)
                                             for path in sorted(paths)}}
    (directory / "generation.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def verify_generation(directory):
    """Reject missing or changed files in a sealed artifact generation."""
    directory = Path(directory).resolve()
    manifest = directory / "generation.json"
    if not manifest.is_file():
        raise ValueError("generation manifest missing; rebuild with python src/build.py")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("artifacts"), dict):
        raise ValueError("invalid generation manifest")
    for name, expected in data["artifacts"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"generation artifact missing or changed: {name}")
    return data


def publish_generation(root, directory):
    """Atomically switch new processes to a complete generation, retaining older snapshots."""
    root, directory = Path(root).resolve(), Path(directory).resolve()
    if not directory.is_relative_to(root / "generations"):
        raise ValueError("generation must be inside the configured generations directory")
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("refusing to replace a non-symlink current directory")
    temporary = root / f".current-{uuid.uuid4().hex}"
    temporary.symlink_to(directory.relative_to(root), target_is_directory=True)
    os.replace(temporary, current)
