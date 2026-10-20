# Local operations and recovery

## Serving

Use one worker: `uvicorn api:app --app-dir src --host 127.0.0.1 --workers 1`.
The application preflight must succeed before `/readyz` returns ready. `/healthz`
only establishes process liveness. Inference capacity and stale-artifact failures
return HTTP 503; clients should retry with backoff. The API is for a trusted local
environment and has no authentication or tenant isolation.

Retrieval weights remain resident; unused CUDA cache is released before answering,
and Ollama unloads the answer model after each response. This allows the stages to
share the 4 GB GPU. The adapter waits for the pinned runner to exit before releasing
the inference slot; the chat response alone does not guarantee memory release.
Each answer therefore includes model reload and unload-wait time. Avoid running
another model workload on the same GPU during benchmarks; the inference slot only
serializes requests inside this service.

## Generations

`build.py` writes a new complete generation and atomically switches `output/current`
only after validation. Current readers retain their old resolved directory until
restart. Keep old generations until the new one has passed the desired live checks.
Do not edit sealed JSON artifacts or execute pipeline stages against a published
generation. Use a separate `CHIPVER_OUTPUT_DIR` for development stages.

For rollback, stop the reader and choose a previously complete generation:

```python
# Run with PYTHONPATH=src from the repository root.
from pathlib import Path
from artifacts import verify_generation, publish_generation
from embed import index_manifest_status
import json

root = Path("output").resolve()
generation = root / "generations" / "REPLACE_WITH_PREVIOUS_ID"
verify_generation(generation)
records = [json.loads(line) for line in (generation / "chunks.jsonl").read_text().splitlines()]
status = index_manifest_status(records, generation / "spec_index", generation / "embed_manifest.json")
if not status["fresh"]:
    raise RuntimeError(status["reason"])
publish_generation(root, generation)
```

Restart the reader and verify readiness and one representative request. A parser-only
generation has no valid specification index and cannot be a serving rollback target.

## Reproduction and backups

Retain source PDFs under the appropriate license, tracked RTL/project manifests,
the exact package lock, model snapshots/digests and each selected `generation.json`.
Model caches and generated corpora remain local; Git contains source, evaluation
labels, documentation and test fixtures. Generation input hashes identify the exact
source/code/lock files used. Chroma compatibility is verified through its logical
inventory, text and dimensions; storage files can change during client maintenance.

Regularly exercise a rebuild and rollback in the intended environment. The repository
does not provide an off-machine backup service, distributed deployment or a formal
hardware verification adapter. Release approval additionally requires independent
label and answer review, as described in [RELEASE_READINESS.md](RELEASE_READINESS.md).
