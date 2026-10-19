# Engineering findings and remediation

Date: 2026-10-19. Scope: the specification/RTL evidence pipeline and its local HTTP
service. The Qianfan-OCR experiment remains optional and is not a production backend.

This is the working record for the senior engineering review. A fix is complete
only when its regression checks pass. Model quality and hardware correctness must
never be inferred from passing software tests.

| ID | Problem | Required outcome | Status |
| --- | --- | --- | --- |
| E01 | Generated RTL search summaries can validate as verbatim source quotes. | Search surfaces and citable source excerpts are separate; quotes resolve to original file/line spans. | Fixed; regression passed |
| E02 | A stale bridge is cached before validation and accepted on a second request. | Every failed bridge load remains rejected; only validated artifacts enter the cache. | Fixed; repeated-load regression passed |
| E03 | Index freshness ignores model identity and actual collection contents. | Manifest binds model revision, prompts, vector dimensions, and exact collection inventory to the corpus. | Fixed; real Chroma regression passed |
| E04 | Gold answers are untracked; dependencies, models, sources and tools are insufficiently recorded. | A clean checkout includes evaluation inputs, a pinned environment, and reproducible build provenance. | Fixed; tracked checkout verified |
| E05 | Stages publish independently and readers can mix artifact generations. | Build a complete isolated generation, validate it, then atomically publish it; readers retain one generation. | Fixed; failed-publication regression and actual rebuild passed |
| E06 | Readiness precedes artifact/model checks; concurrent first requests duplicate model initialization. | Runtime initialization is synchronized, inference is bounded, and readiness requires successful preflight. | Fixed; startup/concurrency checks passed |
| E07 | Fast mocked tests and seven unreviewed held-out candidates do not establish live answer quality. | Keep honest release gates, add real-model integration coverage and an independent review workflow. | Infrastructure fixed; independent human review still required |
| E08 | RTL source resolution drops directories and source discovery assumes a flat directory. | Preserve compiler source paths and accept an explicit project source manifest. | Fixed; nested-source and manifest regressions passed |
| E09 | Architecture and operational documentation describe removed implementations. | One current architecture, accurate entrypoint documentation, tested commands, and clearly archived historical records. | Fixed; current/archived documentation links checked |
| E10 | The real model can emit invalid/truncated JSON or alter quotes while retyping source text. | Constrain generation to visible source labels, copy original passages in the adapter, and reject provider truncation. | Fixed; real-model two-question rerun passed |
| E11 | Repeated links for one signal exhaust the bridge budget and hide another asked identity. | Preserve selected-name priority and allocate a slot per name before additional sites. | Fixed; budget regression passed |
| E12 | Signal navigation loads a GPU entity model without measured benefit over source matching. | Compare on the same development corpus and retain the simpler selector if coverage holds. | Replaced after a paired 60-question comparison; see [measurements](SIGNAL_SELECTION_EXPERIMENT.md) |
| E13 | Family recall collapses different bare numeric table labels into one family. | Preserve table identity and group only windows belonging to the same table. | Fixed; numeric-label regression passed |
| E14 | Native generation accepts a schema-valid selection that omits a required source domain. | Constrain selection to the required domains or an empty abstention, retaining independent source validation. | Fixed; regression, real-provider replay and ten-question validation passed; see [schema experiment](ANSWER_SCHEMA_EXPERIMENT.md) |
| E15 | A known local entity's presence question is treated as standards conformance, forcing an irrelevant SPEC quote. | Route narrowly worded known-entity presence to RTL, preserving comparison for behavior/correctness and explicit standards requests. | Fixed; generic-name/conformance regressions and native RTL-only presence check passed; development intent label corrected |

## Evidence captured before changes

- The first stale bridge load raised `ArtifactFreshnessError`; the next returned
  the same stale bridge.
- An empty index directory plus a text-only manifest was reported fresh, even
  after changing the configured embedding model.
- A generated `terms: arithmetic logic unit ...` line validated under an RTL
  file/line citation despite not occurring in the original source.
- Two simultaneous cold requests constructed two specification retrievers.
- Nested `a/common.sv` and `b/common.sv` sources disappeared from file resolution.
- `eval/gold_questions.json` existed locally but was absent from Git.

## Measurement baseline

The local four-book corpus contains 1,328 PDF pages. The completed development
retrieval benchmark has 60 questions: specification section recall@5 is
0.6979166667 and dual-domain evidence hits@5 are 0.65. These are development
retrieval metrics, not answer accuracy or a release approval. The pre-remediation
working tree passed 288 software tests.

The answer benchmark was interrupted when the user paused the OCR experiment.
Qianfan-OCR inference and the complete OCR comparison have not run. No OCR quality
or throughput result exists, and the active ingestion implementation is not
deprecated.

## Acceptance and remaining external work

Regression tests must cover source-quote integrity, repeated freshness failures,
model/index compatibility, failed generation publication, simultaneous startup,
readiness, and nested RTL sources. Run the full offline suite and a clean-checkout
test. Record any live checks separately with their model/tool/source identity.

Independent experts must approve held-out labels, answer reviews and release
thresholds. Simulation/formal checking is required for behavioral hardware
verification; ChipVer currently returns evidence excerpts and unverified
comparisons. Neither requirement can be replaced by agent-authored approvals.

## Remediation verification

- The hardening milestone passed 304 tests. Subsequent selector and benchmark
  regressions are recorded with their final validation in the [experiment log](SIGNAL_SELECTION_EXPERIMENT.md).
  The real-model suite is opt-in.
- A staged checkout without private PDFs, model weights or generated corpus files
  passes its offline tests; corpus-presence/live checks are explicitly skipped.
- A complete four-book rebuild publishes a verified generation. PDF chunk bytes
  remain identical to the pre-remediation corpus; RTL records now carry original
  source excerpts separately from lexical summaries.
- Initial native-model smoke runs exposed invalid JSON and altered quotes. Both
  failed closed. The final label-selection adapter passed both real-model requests
  in a 63-second run, with roughly 20 seconds per model call on this machine. This
  is integration evidence, not answer-accuracy or throughput certification.
- Every production module now states its responsibility and task flow. Current
  documentation is separated from historical measurements and superseded contracts.
- Independent held-out review, behavioral hardware verification and the complete
  OCR comparison remain outstanding and are not represented as completed fixes.
