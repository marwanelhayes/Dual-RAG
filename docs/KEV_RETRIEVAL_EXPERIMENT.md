# Kev conditional retrieval experiment

This experiment branches from `e4e9946` on
`experiment/kev-routed-retrieval`. The original implementation remains on
`experiment/qianfan-ocr-ingestion`. Only question routing and lane selection change;
signal identity matching and corpus ingestion retain their existing implementations.

## Graph behavior

Set `CHIPVER_RETRIEVAL_MODE=kev` before starting a process to use Kev instead of the
regex classifier. `all`, the default, retains the original two-lane baseline.

| Effective route | Search books | Search RTL | Look up bridge |
| --- | --- | --- | --- |
| SPEC | Yes | No | No |
| RTL | No | Yes | No |
| COMPARISON | Yes | Yes | Yes |

`src/kev_router.py` sends the question and three fixed route definitions to a local
Kev System One server. It validates the selected label, complete finite probability
distribution, expected model alias, and absence of input truncation. Before using
the server it checks `/v1/models` for the exact configured checkpoint and disabled
silent truncation. API startup performs that check before readiness.

The highest-scoring route is used when its probability is at least 0.7. Otherwise
the effective route is COMPARISON and both lanes run. Service failures and invalid
responses also use COMPARISON, with an explicit error reason. There is no regex
fallback in Kev mode. Trace records preserve the raw selection, probabilities,
effective route and fallback. The initial threshold is fixed before measurement;
it is not a calibrated confidence guarantee for this corpus. Override it with
`CHIPVER_KEV_MIN_PROBABILITY` only as a separately recorded experiment.

Existing source validation remains in force. Comparisons require citable evidence
from both domains. A wrong confident route can hide relevant evidence: that risk is
the reason for a paired benchmark rather than assuming a classifier is better.

## Pinned model and serving

- Publisher: [jaredpalmer/kev](https://github.com/jaredpalmer/kev).
- Serving source: `1f04cfa28e6a708485b28a9585010b9e436b776e`.
- Adapter: `jaredpalmer/kev-0.8b`, revision
  `bf75a6a8848ea6960ff2ed108d9ed44c2941174f` (resolved from the publisher's `v1.0` tag).
- Base: `Qwen/Qwen3.5-0.8B-Base`, revision
  `dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68`, as recorded in the checkpoint.
- Checkpoint temperature: approximately 2.3511; use its default calibration.

Kev scores supplied options without generating an answer. It has its own Qwen base
weights; it does not call ChipVer's Qwen3-4B answer model. The paired experiment
loads only that answer model's tokenizer for unchanged context budgeting.

Keep Kev's serving environment separate from ChipVer's dependency lock. ChipVer
talks HTTP using its existing `httpx` dependency. Do not install the unrelated
PyPI package named `kev`. A source installation can follow the publisher's workflow:

```bash
git clone https://github.com/jaredpalmer/kev.git /tmp/chipver-kev
git -C /tmp/chipver-kev checkout 1f04cfa28e6a708485b28a9585010b9e436b776e
cd /tmp/chipver-kev
uv sync --extra serve
KEV_DTYPE=bf16 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
  uv run --extra serve python -m kev.serve \
  --run jaredpalmer/kev-0.8b@bf75a6a8848ea6960ff2ed108d9ed44c2941174f \
  --device cpu --host 127.0.0.1 --port 8009
```

CPU serving is the initial local experiment so Kev does not compete with the
retrievers for the 4 GB GPU. `CHIPVER_KEV_HOST` changes the local endpoint. HTTP
requests have a bounded 30-second timeout. The actual server reports its device,
dtype, checkpoint, temperature and backend in the benchmark provenance.

## Reproduction and evaluation

With the local server ready, run from the ChipVer repository root:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  .venv/bin/python src/kev_bench.py
```

The benchmark explicitly constructs one `all` runtime and one `kev` runtime. They
share the same retrievers, corpus, top-k settings and source validator. It warms
both retrievers, the route service and context tokenizer, then alternates execution
order per question. All 60 development questions run through both graph modes.
The immutable gold labels are not used as input to Kev. No native answer requests
are made. The script requires a successful model preflight, so a missing server
cannot silently become a reported Kev result.

The report at `output/experiments/kev-routed-retrieval/report.json` records:

- Effective and raw routing accuracy, fallback reasons and per-question probabilities.
- SPEC/RTL retrieval scores, context coverage and comparison evidence hits.
- Actual lane calls, warm latency, full graph traces and missed expected chunks.
- Exact dataset hash, corpus generation, current source hashes, corpus-build source
  hashes, Git commit and server details. The existing sealed corpus is reused;
  routing changes do not rebuild or republish ingestion artifacts.

For regular application testing, set `CHIPVER_RETRIEVAL_MODE=kev` on the existing
agent/API command. The general `bench.py --retrieval` command writes
`kev_retrieval_bench.json` in Kev mode, preserving the original baseline report.
Prefer `kev_bench.py` for the paired experiment. Unset the environment variable to
return to baseline behavior.

The 60 questions are development data. Independent paraphrases, reviewed labels,
task-specific probability calibration and answer-quality testing are necessary
before promoting a router. Passing software tests does not establish model quality.

## Results

The full offline software suite passes: **335 passed, 1 skipped**. Tests cover
actual skipped-lane graph paths, fresh state across questions, mandatory comparison
domains, malformed probabilities, service failures and scoring a wrong route as
lost retrieval evidence. Current documentation links resolve.

Local model execution and the paired retrieval measurement are pending. No
performance or quality improvement is claimed before that run completes.
