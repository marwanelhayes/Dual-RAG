# Signal selection and bridge-budget experiment

Date: 2026-10-19. Machine: RTX 3050 Laptop GPU, 4 GB VRAM. This is a development
comparison, not an independent release evaluation. The production embedding,
reranking and answer models were retained.

## Problems and changes

GLiNER2 selected specification names already present in the extracted signal
inventory. Its model cost about 438 MB of CUDA allocation and 196 ms per selection
in a repeated measurement. Literal matching was worth testing because navigation
requires identifiers, rather than inferred behavioral equivalence.

A first case-insensitive matcher incorrectly selected inventory names such as `IS`
and `When` from ordinary prose. The retained matcher requires the source spelling,
then admits identifier-shaped names, address-bearing registers, or names belonging
to a retrieved chunk/section. Question order sets priority; duplicate names prefer
the actual retrieved table occurrence. No corpus-specific word blacklist is used.

Separately, the bridge's global row limit could include twelve links for one name
and hide another explicitly asked name. The fix preserves selector order, prefers
the selected source record, and includes one row per name before additional sites.
Regression tests reproduced the clipping and now verify the corrected result.

## Controlled measurements

Forty specification-bearing development questions reused their eight retrieved
passages from the frozen baseline. Native selection ran twice per question; final
deterministic selection ran ten times per question. Among 19 literal, source-spelled
question identifiers, native selection retained 17 and the final matcher retained
19. This diagnostic checks identity retention, not semantic precision. A separate
bridge-budget diagnostic retained these asked names with existing accepted targets:

| Selector | Original budget | Per-name budget |
| --- | --- | --- |
| GLiNER2 | 5/11 | 9/11 |
| Deterministic | 6/11 | 11/11 |

These counts do not independently validate the underlying bridge mappings.

The main paired run used both real retrieval lanes and the same loaded runtime for
all 60 development questions, alternating selector execution order each question.
Both variants used the corrected per-name bridge budget. Answer generation was
explicitly skipped to isolate navigation and retrieval. Every question had identical
lane scores and expected-evidence context coverage across selectors.

| Measure | GLiNER2 | Deterministic |
| --- | --- | --- |
| Spec section recall@5 | 0.6979167 | 0.6979167 |
| RTL exact-ID recall@5 | 0.5750000 | 0.5750000 |
| Dual-domain evidence hits@5 | 0.6500000 | 0.6500000 |
| Context spec section recall | 0.7604167 | 0.7604167 |
| Context RTL exact recall | 0.6583333 | 0.6583333 |
| Warm mean retrieval, excluding first question | 2.8095 s | 2.5921 s |
| Median retrieval, all questions | 2.8368 s | 2.6036 s |
| Mean context length | 19,609.6 chars | 19,706.7 chars |
| Repeated selector mean | 196.27 ms | 0.64 ms |

Warm retrieval improved 7.7% in this run. The first native question also initialized
shared retrievers and GLiNER2; its 25.67 seconds cannot isolate native cold-start
cost. The deterministic first question took 2.45 seconds on already-loaded shared
retrievers, so these are not comparable independent cold starts. The separate
GLiNER2 load measurement was 7.46 seconds. Timings are local observations.

The final code removes GLiNER2 loading/configuration and its `gliner2`/`peft`
requirements. Tests now exercise actual selection instead of replacing a tagger.
The simpler selector is retained based on these development results.

## Artifact identity and verification

The paired run used generation `7e61f16f0b104cdb93b3a3d57bf333cb` with the
pre-removal GLiNER2 implementation from commit `551eb10`, plus the bridge-budget
fix shared by both variants. GLiNER2 revision:
`f9634218e53580c56edf0de97ca1a7d3f1c2354e`.

- Corpus SHA-256: `86345072bfc64af230e3f548565d15d81ddead5ab9ef4bf5469d8065049b06d4`.
- Question-set SHA-256: `87e74da669b33906113b6015cda6aaeeb145fda189855ed88765412db7e3519b`.
- Paired report SHA-256: `f38ecb6197edc2040d6ab542ed776f2bdf39a43d544130509c854df1db5c2a3f`.
- Final selector source SHA-256: `ec3c91dd2e741ff0534947d8a838c3058a1e70b55a4634734216a6fcafdb66cd`.

Raw reports remain in that local generation: `paired_selector_bench.json`,
`signal_selection_experiment.json`, `signal_selection_variants.json`,
`signal_bridge_budget_experiment.json`, and `exact_source_selection_bench.json`.
They are experiment outputs, not sealed corpus inputs. Private corpus data and
model weights remain outside Git. Use [the pipeline commands](../PIPELINE.md) for
another current-code measurement.

Verification of the retained code:

- Full software suite after the additional schema/intent corrections: 312 passed, one
  opt-in live check skipped.
- Staged checkout without the private corpus: 310 passed, three corpus/live checks skipped.
- Full build published generation `7c575f4040a24e9d9d561ae7aee57ef3`; all seven
  sealed corpus artifacts are byte-identical to the comparison generation. The
  new generation's recorded source hashes match the retained production code.
- The current-code 60-question graph and standalone-lane rerun completed. Graph
  section/exact-ID scores remained unchanged for every question; overall graph
  spec section recall@5 is 0.6979167 and dual-domain evidence hits@5 are 0.65.
  Mean graph latency including its cold start was 2.734 seconds. These scores
  leave retrieval coverage gaps and are not a claim that quality is optimal.

The complete native answer run then exposed a separate domain-selection defect.
Its [reproduction and correction](ANSWER_SCHEMA_EXPERIMENT.md) are recorded
separately from selector performance. The final schema build published generation
`8fba5c6a96d04b4cac075195747c676e`, again with all seven corpus artifacts unchanged
and matching production source hashes. The schema-only correction leaves retrieval
and prompt budgeting unchanged; the 60-question measurements above refer to the
explicitly identified selector build.

The subsequent known-entity presence correction published generation
`b51bba4f29eb4723855fa3d0f0cb0264`; all seven corpus artifacts again remained
byte-identical and source hashes matched. All 60 development retrieval questions
retain their original routing. One ten-question answer-set intent label changed,
as documented in the native answer experiment; the held-out candidate was untouched.
All ten final native answer requests passed source/domain validation. Five remained
unverified by the keyword diagnostic; no independent answer-quality claim is made.

During verification, a separate benchmark defect was reproduced: bare numeric
table IDs such as `doc#table#25` and `doc#table#26` collapsed to one family. The
family metric now preserves the table label and joins only windows of that table.
The paired report predates this diagnostic correction; section/exact-ID metrics
above are unaffected. A current-code rerun records the corrected family metric.

Independent held-out answer review, model-family comparisons and the paused OCR
experiment remain outstanding. Neither these measurements nor passing source
validation establishes optimality or behavioral hardware correctness.
