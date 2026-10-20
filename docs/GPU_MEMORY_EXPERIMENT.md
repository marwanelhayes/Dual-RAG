# Reranker loading and GPU memory

Dates: 2026-10-19–20. Hardware: RTX 3050 Laptop GPU, 4 GB VRAM. Models, revisions,
corpus, batch size and answer schema were retained. This experiment changes how
the existing float16 reranker is loaded, not its precision during inference.

## Measurements before changing production code

First, three development questions covered SPEC, RTL and COMPARISON intent. Each
ran with and without cache release after real retrieval, alternating execution
order. The configured answer model was unloaded between cases so model placement
could respond to available memory. Both conditions used the same loaded retrieval
models and ran another retrieval while the answer model remained resident.

Cache release freed at most 6.3 MB. Mean native answer time was 18.30 seconds
without it and 18.58 seconds with it. All six responses passed source validation,
selected citations matched per question, and every following retrieval succeeded.
This small experiment did not justify cache release alone with the old loader.
The broader workload below later exposed a different cache-pressure case.

Allocator inspection then found about 1,054 MB in inactive split blocks after
the current loader initialized and cache release ran. The reranker was first
loaded onto CUDA in float32, then converted to float16. A fresh-process comparison
tested loading directly in float16 through the supported model constructor.

| Measure | Load float32, then convert | Load float16 directly |
| --- | --- | --- |
| Active CUDA tensor bytes after warmup | 1,277,612,032 | 1,277,612,032 |
| Reserved bytes after warmup and cache release | 2,332,033,024 | 1,308,622,848 |
| Inactive split bytes at that point | 1,054,420,992 | 31,010,816 |
| Reserved bytes after the SPEC query | 2,332,033,024 | 1,774,190,592 |
| Answer-model GPU allocation reported by Ollama | 927,209,984 | 1,483,481,600 |
| Mean retrieval time across three cases | 2.789 s | 2.803 s |
| Mean native answer time across three cases | 17.785 s | 16.256 s |

All retrieved IDs and selected citations matched for each paired question. All
responses passed source validation and all following retrievals succeeded. Native
answer time fell 8.6% in this exploratory comparison. These are sequential local
measurements with model reloads, not a throughput guarantee or a controlled warm
service benchmark. Model placement and timings depend on available GPU memory.

The comparison used production code from commit `f981e2e` and overrode only the
constructor dtype for the direct-load variant. The old subsequent `.half()` was
then a no-op for model parameters. The retained implementation sets the CUDA dtype
at construction and removes that subsequent cast; CPU loading keeps its default
precision. A constructor-boundary regression failed before the fix and passed after.

## Full-workload failure

Loading directly in float16 alone is insufficient. A single runtime completed all
60 development retrieval questions with identical ranked results to the baseline,
then ran the ten native answer cases without unloading between requests. PyTorch
reserved 3,462,397,952 bytes although live tensors occupied only 1,277,612,032 bytes.
Ollama reported zero GPU allocation and logged that no model layers fit on the GPU.
The first native answer took 270.976 seconds and passed source validation; the other
nine hit the 300-second provider timeout and were rejected.

The old keyword scorer misleadingly counted three of these rejected responses as
correct because those questions accepted abstention. They are provider failures,
not honest abstentions. The corrected development benchmark records rejected
responses as `error` and includes their validation details. The original failed
report is retained as negative evidence, without rewriting its historical summary.

A follow-up probe released unused CUDA cache before generation. Reservation fell
to 1,308,622,848 bytes, and Ollama placed 20 of 37 layers on the GPU. The first
answer passed source validation in 72.225 seconds including retrieval. The next
retrieval then failed with CUDA out-of-memory: Qwen was still occupying about
2.04 GB. This reproduces why cache release alone is insufficient. Its partial report
is explicitly incomplete; it is not a passing two-question benchmark. Software tests
ran during part of this probe, so its timing is not a controlled comparison.

Requesting `keep_alive: 0` alone also failed the immediate follow-up check.
Generation `094e9467c8db46cc9d717b41e9b0020e` completed all 60 retrieval cases,
then its first native call returned while Qwen still held about 2.04 GB. The
immediately following retrieval failed with CUDA out-of-memory. A later process-list
request showed that Qwen had eventually exited. The final candidate waits for that
exit before returning, including after a provider exception. The polling deadline
is ten seconds; a stalled unload rejects the answer instead of silently proceeding.
A focused two-question rerun with the bounded wait passed both native source
validations and both immediately following high-memory retrievals. Ollama's process
list was empty after each cycle. Latency was contended: four unrelated Manim renders
saturated the CPU, with host paging observed. Its 58.568- and 107.385-second native
request times are diagnostic observations, not a controlled performance comparison.

These changes retain model inference precision and model identities.

## References and artifacts

[PyTorch's allocator documentation](https://docs.pytorch.org/docs/stable/notes/cuda.html#memory-management)
distinguishes active tensors from reserved cache and explains that cache release
does not free occupied tensors. The measured inactive split blocks explain why
cache release alone did not reclaim this reservation.
[Ollama documents `keep_alive: 0`](https://docs.ollama.com/api/chat) as unloading
the model after generation. In the pinned provider, the
[scheduler implementation](https://github.com/ollama/ollama/blob/v0.18.3/server/sched.go)
processes expiration asynchronously and removes the loaded runner after closing it.
This explains why the chat response is insufficient as a handoff barrier.
[Sentence Transformers](https://www.sbert.net/docs/cross_encoder/usage/efficiency.html)
supports loading the cross-encoder in float16 through model arguments;
[Transformers' loader](https://huggingface.co/docs/transformers/main_classes/model)
accepts the `dtype` argument used by the pinned environment.

Raw reports remain in local generation `b51bba4f29eb4723855fa3d0f0cb0264`:
`gpu_cache_probe.json`, `gpu_dtype_converted.json`, and `gpu_dtype_initial.json`.
The scripts and reports are retained with the local MLflow experiment. Private
corpus contents and model weights are excluded from Git.

The direct-loading-only generation `853f2f3e878f433fa0c4768f1c72f076` retains
`gpu_fp16_without_handoff.json`, the incomplete `gpu_handoff_probe.json`, and the
passing retrieval report. The failed probe's traceback is preserved with its script.

Generation `094e9467c8db46cc9d717b41e9b0020e` retains the asynchronous-unload
failure and its complete retrieval report. All seven sealed corpus artifacts are
byte-identical to the previous corpus. The final bounded wait is verified separately;
passing pre-wait software tests did not establish safe GPU handoff.

The retrieval comparison checks ranked base IDs and their labeled coverage. RTL
continuation windows can share a base ID while supplying different source passages;
prompt character counts varied in several repeated cases despite matching base IDs
and labeled context recall. This comparison does not establish identical prompts
or semantic answer quality.

## Final candidate verification

The bounded-wait implementation rebuilt generation
`08b16f75c0a74e4092568c778d824d5c`. Its input hashes match the current production
source, and all seven sealed corpus artifacts match the preceding corpus exactly.
The full software suite passes 317 tests, with one live check skipped. A clean
export of staged tracked files passes 315 tests, with three corpus/live checks
skipped. Current documentation file links pass their existence check.

The first final run completed all 60 ranked retrieval comparisons without changes.
It was interrupted during native generation at the user's request to wait for
competing Manim renders. That report and its interruption log are preserved under
separate names; they do not represent a completed native benchmark. The queued full native run was then canceled at the user's request. The requested
final benchmark runs retrieval only: all 60 questions through the agent graph and
all 60 through the standalone retrieval stages. Qwen weights are not loaded and no
answer generation runs; the cached Qwen tokenizer still budgets the graph's context.
Timings are collected alongside active renders and are not a controlled speedup
comparison. Expanded native latency and ten-case mixed-workload validation are
deferred. The earlier two-cycle native handoff probe remains the candidate's
real-provider verification.

## Completed retrieval-only benchmark

Command: `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python src/bench.py --retrieval`.
Generation: `08b16f75c0a74e4092568c778d824d5c`; production source commit: `ce33684`.
All 60 agent-graph questions and all 60 standalone-stage questions completed.
Every ranked SPEC/RTL lane result matched the corresponding result in baseline
generation `7c575f4040a24e9d9d561ae7aee57ef3`; there were zero changed lane rows
in either pass. This is preservation of labeled retrieval behavior, not perfect
retrieval or independently measured answer accuracy.

| Agent retrieval measure | Result |
| --- | --- |
| Routing accuracy | 100% |
| SPEC section recall@5 | 69.79% |
| SPEC exact-ID recall@5 | 60.42% |
| RTL family recall@5 | 78.33% |
| RTL exact-ID recall@5 | 57.50% |
| Dual-domain evidence hits@5 | 65.00% |
| Mean graph request time, including the first request | 3.054 s |
| First request, including lazy initialization | 20.748 s |
| Warm mean, excluding the first request | 2.754 s |
| Median graph request time | 2.851 s |

These graph times include retrieval, bridge lookup and context assembly, with
answer generation disabled. The standalone stages preserve their own baseline
metrics; their RTL exact-ID recall@5 is 56.67% because they request five records
directly, whereas the graph retrieves a larger shortlist. Neither value is an
answer-quality score. The render workload was active during measurement, so these
wall times do not establish a controlled speed improvement or regression.

The full software suite passes 317 tests (one skipped), the clean staged export
passes 315 (three skipped), and [GitHub CI passed for the source commit](https://github.com/marwanelhayes/Dual-RAG/actions/runs/39368207065).
The prior scheduled Qwen run is canceled. Expanded native answer validation and
native latency measurement remain deferred at the user's request.

Raw results are in the generation's `retrieval_bench.json`; the comparison,
source hashes and timing scope are in `gpu_retrieval_only_verification.json`.
Experiments, negative results and scripts are retained in local MLflow run
`ab07cd1567954ccdbf89640dd84516dd`. No book text or model weights are committed.

Independent answer-quality review and alternative-model evaluation remain separate
requirements. The optional OCR experiment remains paused.
