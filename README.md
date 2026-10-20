# ChipVer

ChipVer finds citable evidence in hardware specifications and SystemVerilog RTL.
It uses compiler-derived RTL structure, independent retrieval lanes, a conservative
identity bridge, and a local model that selects source excerpts. It does not prove
implementation correctness, absence, completeness, or protocol conformance.

Start with [the pipeline guide](PIPELINE.md), [the current architecture](docs/ARCHITECTURE.md),
and [the engineering findings](docs/ENGINEERING_REMEDIATION.md).
The [operations guide](docs/OPERATIONS.md) covers readiness, rollback and reproduction;
[model evaluation notes](docs/MODEL_EVALUATION.md) distinguish candidates from measured results.

## Setup

The validated environment uses Python 3.12, Verilator 5.032, and the exact packages
in `requirements.lock`. That lock captures the Linux CUDA environment, including
optional OCR dependencies; it is not a portable CPU-only lock. `requirements.txt`
lists the direct dependencies for deliberate environment updates.

```bash
python3.12 -m venv .venv
.venv/bin/pip install --no-cache-dir -r requirements.lock
verilator --version
```

Provision licensed PDFs in `Ingest/`; they remain local and are excluded from Git.
The RTL corpus is tracked under `rtl/`. Model IDs and exact revisions are in
`src/configure.py`. The first build needs network access to download those pinned
weights; later runs can use the local Hugging Face cache.

```bash
.venv/bin/python src/build.py --clean
ollama serve
# In another terminal, install the configured qwen3:4b model if needed.
ollama pull qwen3:4b
.venv/bin/python src/agent.py "Where is risc_mux2 implemented in the RTL?"
```

The service requires the exact configured Ollama model digest. A different digest
needs an explicit configuration update and reevaluation, not an automatic bypass.
Run one service worker on the small local GPU:

```bash
.venv/bin/uvicorn api:app --app-dir src --host 127.0.0.1 --port 8000 --workers 1
```

This is a trusted local service without authentication. Do not expose it to an
untrusted network. Startup checks the corpus, index, models and tokenizer before
readiness succeeds; simultaneous inference requests receive HTTP 503.

## Checks and evaluation

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/pytest -q
.venv/bin/python src/bench.py --retrieval
.venv/bin/python src/bench.py
.venv/bin/python src/track.py --tag your-change
CHIPVER_LIVE_TESTS=1 .venv/bin/pytest -q tests/test_live_pipeline.py
```

The regular suite uses deterministic test adapters and needs no downloaded models.
Live checks use the real retrievers, tokenizers and local answer model. They are smoke
checks, not an independent answer-quality certification. Follow
[the release workflow](docs/RELEASE_READINESS.md) for reviewed labels and answers.
The [GPU memory experiment](docs/GPU_MEMORY_EXPERIMENT.md) documents how retrieval
and native answering share the local 4 GB GPU, including failed approaches and
the limits of latency comparisons under competing workloads.

Qianfan-OCR is a paused, optional [experiment](docs/specs/ocr_ingest.md), with no
production callers and no completed quality/throughput comparison.
