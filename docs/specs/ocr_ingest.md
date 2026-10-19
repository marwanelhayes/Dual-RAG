# Local Qianfan-OCR ingestion experiment

`src/ocr_ingest.py` renders the source PDFs and runs the open-weight
[`baidu/Qianfan-OCR`](https://huggingface.co/baidu/Qianfan-OCR) locally through
native Transformers support. It does not call Baidu's hosted API. The model
revision is pinned in the source; `--four-bit` enables NF4 quantization.

Each source page becomes a separate Markdown file, cached under the source PDF's
SHA-256 and the model revision, precision, prompt, generation limit and rendering
resolution. Interrupted runs reuse completed pages. Empty or truncated model
outputs fail before being cached. PDFs supply rendered pixels and page counts;
headings, captions, text and tables come from Markdown, without PDF outlines,
font heuristics or PDF table extraction.

The parser accepts ATX and Setext headings, pipe tables and HTML tables, including
rowspans and colspans. Captioned continuations join under their original table
label; uncaptioned adjacent pages join only when their column headers agree.
Code fences are prose, not headings or tables. Images are excluded. The existing
chunking, hierarchy, role scoring, provenance fields and encoder limits are
shared with PDF ingestion. Page citations refer to source PDF page numbers;
multi-page table windows conservatively cite the complete table span.

Install the optional dependencies with:

```bash
.venv/bin/pip install -r requirements-ocr.txt
```

Run from the repository root. Use the same installed model versions, GPU and
configuration for both paths. `CHIPVER_OUTPUT_DIR` isolates every downstream
artifact, including vector indexes, from the active corpus. Full builds create and
publish new generations; direct OCR stages below use scratch output and do not publish.

```bash
export CHIPVER_OUTPUT_DIR=output/ocr_comparison/baseline
.venv/bin/python src/build.py
.venv/bin/python src/bench.py --retrieval
.venv/bin/python src/bench.py
.venv/bin/python src/track.py --tag ingest/qianfan-baseline

export CHIPVER_OUTPUT_DIR=output/ocr_comparison/qianfan
# Stop the answer model before loading OCR on a small GPU.
ollama stop qwen3:4b
.venv/bin/python src/ocr_ingest.py --four-bit \
  --output-dir "$CHIPVER_OUTPUT_DIR" --cache-dir output/ocr_markdown
for stage in spec_signals embed rtl_ingest rtl_embed bridge; do
  .venv/bin/python "src/$stage.py" || break
done
# Continue only after every rebuild stage succeeds.
.venv/bin/python src/bench.py --retrieval
.venv/bin/python src/bench.py
.venv/bin/python src/track.py --tag ingest/qianfan-ocr
unset CHIPVER_OUTPUT_DIR
```

For the weights downloaded during this experiment, set
`HF_HOME="$PWD/output/ocr_model_cache"` only on the OCR command. The other stages
use the existing embedding/reranker/tagger model cache.

Compare the complete books and both benchmark reports. The repository's ingestion
decision metric is specification **section recall@5**, which survives re-chunking;
exact chunk-ID recall is diagnostic only. Also compare ingestion wall time,
table rows, unresolved references, prose/table overlap, token-limit violations,
signal and bridge metrics, and full answer results. Report cold OCR time separately
from cached Markdown parsing and model download time. A partial or failed run is
inconclusive, never evidence of improved or degraded quality.

`ocr_ingest.py` currently has no production callers. Only after a completed
comparison shows an improvement should it replace `ingest.py`, with the previous
implementation retained as `depricated_ingest.py`. That promotion also needs its
shared-helper import changed to `depricated_ingest`, the public `content_hash` and
`load_chunks` exports preserved for downstream callers, the CLI output default
changed to `cfg.OUTPUT_DIR`, and tests pointed at the corresponding implementations.
If quality degrades, keep `ocr_ingest.py` unconnected and retain the PDF entrypoint.
