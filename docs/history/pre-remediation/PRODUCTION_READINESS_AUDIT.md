> Historical record archived on 2026-10-19. Commands, metrics, and descriptions below are not the current contract. See [current architecture](../../ARCHITECTURE.md).

# ChipVer production-readiness audit

Audit date: 2026-09-09

Scope: all Python production modules, tests, repository/deployment metadata, generated artifact
health, the current 60-question retrieval benchmark, the current held-out candidate, and the
saved answer evaluation. The RTL's functional correctness was not re-verified with simulation
or formal methods. No current CVE or license scan was available in the repository.

## Verdict

ChipVer is a strong research/internal-pilot implementation, but it is not production-ready
today. The deterministic parsing, provenance, artifact freshness checks, and fail-closed
answer schema are good foundations. Release evidence, retrieval quality, route quality,
service operations, artifact publication, and security controls still have blocking gaps.

## Status update (2026-10-08, branch `simplify`)

The findings below are left as audited. These have since changed:

- **Resolved #8**: the vector store is Chroma; no pickle is loaded (`embed/chroma`).
- **Resolved #20, #28**: lane calibration, floors, and credibility gates were removed (DF-31).
- **Resolved #24**: one cross-encoder per process serves both lanes and the bench
  (`retrieve/shared-reranker`); the RTL lane loads no embedder (`retrieve/rtl-lexical-only`).
- **Partial #38**: `.v`, `.sv`, `.svh`, and `.vh` sources are read
  (`rtl_ingest/verilog-sources`); paths are still resolved by basename in one directory.
- **Partial #39**: the top module and package files are found from the sources, with
  `RTL_TOP_MODULE` as an override (`rtl_ingest/detect-top`); include paths, defines, and
  parameters still have no project manifest.

## What passed in this audit?

- All 192 tests passed in 6.35 seconds.
- The installed Python environment reported no broken dependency requirements.
- Both current FAISS manifests match their current record sets: 5,406 spec records and 154
  RTL records.
- The bridge fingerprints match its three current inputs.
- The fresh retrieval benchmark completed all 60 questions without query-model calls.
- Focused whitespace checks pass for the new explanatory documentation.
- The answer validator rejects unknown citations, altered quotes, unsupported schemas, and
  missing comparison domains.

These results show deterministic code health. They do not override the release gate or the
quality findings below.

## Release blockers

### 1. The release gate is explicitly failing

The current held-out file is still marked heldout_candidate. It has no reviewed_by value, no
thresholds_approved_by value, and no approved threshold map. Its seven cases contain only four
answerable and three unanswerable questions. This is too small and unapproved for a deployment
claim.

The saved release report belongs to a different dataset hash. Current dataset hash:

    fb92d0d86695d68618a0cf73d0163139d56768b3132dd95572263f4d1bd4189a

Saved report hash:

    625c06b27ade55329df0e5e0fa7a0960b7f4ed78ccb563f3dbb8e6db964e200a

The saved result therefore cannot be rescored against the current candidate. A fresh run and
new human review are mandatory.

### 2. Retrieval quality is below a defensible production bar

The fresh 60-question benchmark reports:

| Measure | Current result |
| --- | ---: |
| Questions missing at least one expected source at rank 5 | 45 of 60 |
| Spec Recall@5 | 45.8% |
| Spec full-hit rate | 32.5% |
| Spec questions with zero expected hits | 7 of 40 |
| RTL Recall@5 | 40.0% |
| RTL full-hit rate | 32.5% |
| RTL questions with zero expected hits | 3 of 40 |
| Comparison questions hitting both domains | 40.0% |

Exact identifier questions perform much better than plain-language questions. Plain spec
Recall@5 is 19.6%; plain RTL Recall@5 is 31.7%. This makes user wording a major reliability
factor.

### 3. Requested-intent classification is unreliable

Overall route accuracy is 63.3%. SPEC cases score 100%, RTL cases 40%, and COMPARISON cases
50%. Searching both lanes preserves reachability, but route_prior still decides which
citations the final answer requires. A misclassified RTL request can therefore be validated
as a spec answer, and a missed comparison can avoid the two-domain contract.

The classifier needs either much broader tested rules or a bounded intent classifier with a
confidence/abstention contract. The release set must contain paraphrases that do not use the
current trigger words.

### 4. Generated-answer behavior has unresolved failures and unacceptable latency

The saved eight-case run is stale, but it still demonstrates operational behavior:

- two answers were rejected by validation;
- one omitted a required domain and one quoted text not present in evidence;
- mean case latency was 91.3 seconds;
- maximum case latency was 174.6 seconds;
- several successful cases took 74 to 175 seconds.

A production target, percentile latency gate, cancellation behavior, and timeout budget have
not been defined. A 300-second model timeout allows one request to occupy capacity for five
minutes.

### 5. The product contract does not answer behavioral-conformance questions

The public answer can return verbatim excerpts and an UNVERIFIED COMPARISON label. It cannot
state implemented, missing, correct, compliant, or contradictory. This is a sound safety
boundary, but it means ChipVer is an evidence assistant rather than an RTL/spec verifier.

If deployment requirements include actual conformance decisions, the product is incomplete
until simulation, formal checks, equivalence, or a reviewed adjudication workflow supplies
that evidence.

### 6. The current RTL corpus has 147 accepted lint warnings

The generated compiler artifact contains:

| Warning | Count |
| --- | ---: |
| WIDTHEXPAND | 58 |
| WIDTHTRUNC | 55 |
| CASEINCOMPLETE | 17 |
| IMPLICIT | 10 |
| ASCRANGE | 4 |
| SELRANGE | 3 |

Implicit nets, incomplete cases, width truncation/expansion, and range diagnostics can change
hardware meaning and pollute the retrieval representation. Verilator is configured with
-Wno-fatal, and no warning policy gates artifact publication. At minimum, implicit/range
warnings should block the corpus; every remaining warning category needs an explicit waiver.

### 7. There is no deployable application boundary

The repository exposes command-line scripts. It has no HTTP/gRPC service, request schema,
authentication, authorization, health/readiness endpoint, process manager, worker model,
rate limit, concurrency limit, graceful shutdown, or deployment manifest. There is also no
container, package metadata, or supported Python-version declaration.

A production claim needs a concrete target: local single-user CLI, an internal authenticated
service, or a multi-user service. The controls differ substantially.

## Security and data-protection gaps

### 8. FAISS loading enables pickle execution

VectorStore.load enables dangerous deserialization for LangChain's FAISS pickle. This is safe
only if the entire output directory is trusted, immutable to untrusted users, and delivered
with verified hashes. Those deployment guarantees do not exist in the repository.

Use a non-pickle metadata format or verify a signed/allowlisted artifact before loading. Run
the serving process with read-only artifacts and a separate unprivileged identity.

### 9. Proprietary source text can leak through traces

trace_report includes final_context, which contains spec passages and RTL source, and the CLI
prints that report to standard error. Central log collection would copy proprietary corpus
content into logs. Production traces should contain IDs, hashes, counts, and timings by
default; full evidence needs a privileged debug mode and retention policy.

### 10. Input size is unbounded

The CLI accepts any question length. The value flows into exact matching, BM25, dense
embedding, reranking, traces, and possibly the model prompt before the final context cap
helps. Add a byte/character limit and reject empty, oversized, or invalidly encoded input at
the service boundary.

### 11. Local-model transport has no production controls

Model calls use plain HTTP to a hard-coded localhost endpoint, create a new client per call,
and have no authentication, TLS, connection pool, retry policy, circuit breaker, request ID,
or response-size limit. Localhost can be acceptable inside one host, but the deployment must
enforce network isolation and bounded concurrency.

### 12. Prompt injection is reduced, not eliminated

The system prompt says source instructions are untrusted, and the output schema prevents
free-form claims. A malicious PDF or RTL comment can still cause the model to select a
misleading but verbatim quote. Provenance validation is not relevance or entailment
validation. Corpus admission, source trust labels, and reviewed answer-quality tests remain
necessary.

### 13. There is no corpus access or retention policy

Generated JSON and FAISS metadata reproduce specification and RTL content. No repository
mechanism defines tenant isolation, filesystem permissions, encryption at rest, backups,
retention, deletion, or audit access. These are required before hosting third-party or
confidential designs.

### 14. Secret handling is informal

A local .env contains a Gemini key even though the code uses Ollama and does not load that
file. The key may be stale, but a production setup should remove unused credentials and use
the deployment platform's secret store. The value was not read during this audit.

## Artifact and rebuild reliability gaps

### 15. Clean rebuild destroys the last good release first

The clean option deletes output before rebuilding. A failure leaves no complete serving
snapshot and no rollback. Build into a versioned staging directory, validate it, then
atomically switch one release pointer while retaining the previous version.

### 16. Related artifacts are not published as one snapshot

Some JSON files use temporary-file replacement, but hierarchy and chunks are swapped
separately. rtl.json, rtl_chunks.jsonl, manifests, FAISS directories, benchmarks, and lane
calibration are written directly. A reader can observe mixed generations or partial files.

A release manifest should name every artifact hash and schema version. Publish the directory
only after all files validate.

### 17. Builds and queries have no locking or generation pinning

Concurrent builds can overwrite the same temporary names and indexes. Queries can run while
a build replaces files. There is no builder lock, reader generation ID, or atomic release
selection.

### 18. Long-lived Runtime objects never refresh

Artifacts and retrievers are cached after first use. Bridge and index freshness are checked
only before first load. A running service keeps the old generation after files change, while
a partial rebuild can make different cached pieces refer to different generations.

### 19. Index freshness omits model and environment identity

Record manifests hash record content but not the embedding model identifier, model revision,
query prefix, normalization flag, vector dimension, library versions, or index format.
Changing one of these without changing records can incorrectly reuse an incompatible index.

### 20. Lane calibration freshness uses only record counts

lane_calibration.json is accepted when the spec and RTL record counts match. Content, model,
reranker revision, quantile, sample count, and calibration algorithm can change without
invalidating it. Calibration publication is also a direct write with no lock.

### 21. Artifact consumers do not validate schemas deeply

Most loaders parse JSON and assume required keys/types. Schema-version fields are written but
not consistently checked on read. Corrupt, truncated, manually edited, or newer artifacts
usually cause request-time exceptions rather than a startup readiness failure.

### 22. Parser-only build can overwrite a production bridge

The no-embeddings mode still rebuilds bridge.json without semantic reranking while leaving
existing indexes in place. This development command shares the production output directory
and can silently change comparison targeting.

### 23. Tool failure detection is incomplete

run_verilator checks output text for a percent-Error marker instead of checking the process
return code. Unexpected nonzero exits can be misclassified. Error output is not preserved as
a structured build report.

## Retrieval, evidence, and model gaps

### 24. Multiple copies of the same models can be loaded

The spec Retriever creates its own Embedder and Reranker. The RTL hybrid creates another
Embedder and Reranker. Runtime.get_reranker creates a third cross-encoder for lane scoring.
This duplicates large weights, GPU memory, startup time, and failure points.

One process-level embedder and one reranker should be injected into both domain retrievers
and lane scoring.

### 25. Every question pays for both domains

Both lanes always run. This protects reachability, but it doubles dense/reranking work for
clear single-domain questions. Keep the safety property with a cheap first pass and retrieve
the second lane only when intent is uncertain, comparison is requested, or evidence
disagrees. Measure the quality/cost tradeoff before changing it.

### 26. RTL citations can quote generated retrieval text

citable_evidence treats an RTL retrieval record's page_content as citable. Module surfaces
contain generated English such as reads, writes, terms, and structure, not only literal
SystemVerilog. A model can therefore return a verbatim quote from generated metadata under a
file/line citation. Restrict public RTL quotes to literal source_code/source_text, or label
compiler-derived summaries distinctly.

### 27. Structured entities are visible but not independently citable

Selected spec signals and bridge rows enter final context, but citable_evidence catalogs spec
chunks, RTL records, and RTL blocks only. The model cannot safely cite a structured
width/direction fact unless the containing source passage also survives intact. Either add
source-backed entity citations or keep structured rows out of answer-facing text.

### 28. Lane floors are heuristic, not calibrated to answer correctness

Background pairs are deterministic mismatches sampled from current text, not human-labeled
relevance examples. The 95th percentile floor measures corpus noise, but no evaluation shows
that clearing it predicts adequate evidence. Release evaluation must measure false-credible
and false-weak lanes.

### 29. The bridge has very low accepted coverage

The current bridge accepts 18 of 650 non-instruction spec entities, leaves 632 unresolved,
aligns 30 of 405 instruction entities, and produces two section/module alignments.
Conservative unresolved output is correct behavior, but it limits the product's useful
comparison coverage. Coverage targets and representative bridge gold labels are needed.

### 30. Answer validation has no minimum semantic content

A one-word verbatim quote can pass. The validator proves containment and domain coverage,
not relevance, completeness, uniqueness, or support for the user's question. Human release
review catches some of this; live requests have no equivalent check.

### 31. Model abstention can hide regressions

The model may return insufficient_evidence even with strong evidence and pass schema
validation. Only a sufficiently broad reviewed fact-coverage gate prevents an
all-abstaining or over-abstaining model from appearing safe.

### 32. Error reporting is too shallow

Several broad exception handlers store only the exception class. Production diagnosis needs
stage, request ID, operation, sanitized message, retryability, duration, and artifact/model
generation. User-facing errors should distinguish unavailable model, stale artifact,
invalid input, timeout, and internal failure.

## Ingestion and corpus-quality gaps

### 33. PDFs without usable outlines are unsupported

Prose chunking iterates outline nodes. A PDF with no bookmarks, malformed page numbers, or a
bad hierarchy can yield no useful prose. There is no fallback page chunking and no per-document
minimum-coverage gate.

### 34. Figures, waveforms, OCR, and multi-page tables are unsupported

The source contains an explicit figure-extraction TODO. Table extraction begins below a
caption on one page. Scanned PDFs, captionless diagrams, waveforms, continued tables, and
cross-page rows can disappear without a release-blocking extraction report.

### 35. Extraction quality has no acceptance thresholds

The build prints counts and average chunk size but does not fail on unknown revision, zero
sections, zero prose, zero tables where expected, implausible page coverage, malformed rows,
or a sudden count change. Persist per-document coverage statistics and compare them with an
approved baseline.

### 36. Document IDs can collide

slugify uses only a normalized filename stem. Names that differ only by punctuation or case
can share IDs and downstream chunk IDs. Validate uniqueness before writing or include a
stable source hash in the namespace.

### 37. Outline-position IDs are not stable under insertion

Section and prose IDs use outline indexes. Adding one earlier heading renumbers all later
records, invalidates gold IDs, and forces avoidable re-embedding. This is deterministic but
not content-stable.

### 38. RTL path handling assumes a flat .sv directory

file_table discards original directories, accepts only .sv, and reconstructs paths by
basename. Nested sources, duplicate basenames, .svh headers, generated sources, external
libraries, includes, defines, and filelists are not represented safely.

### 39. RTL compilation is corpus-specific source code

The top module and exact Verilator inputs are constants in configure.py. Supporting another
design or build configuration requires editing code. A production builder needs a validated
project manifest with top, file list, include paths, defines, parameters, and tool version.

### 40. Warning parsing is partial

Only one warning line format is recognized. Multiline context, notes, errors, and diagnostics
from other Verilator versions are discarded. Store the raw tool log plus structured parsed
diagnostics and the exact command/tool version.

## Deployment and operability gaps

### 41. No health, readiness, or startup preflight exists

The service-to-be needs startup checks for every file/hash/schema, both model revisions,
vector dimensions, Verilator/build provenance, available memory/disk, and one bounded
retrieval smoke query. Readiness must fail before accepting traffic.

### 42. No production observability exists

There are prints and developer traces, but no structured logs, metrics, tracing, request IDs,
redaction, dashboards, or alerts. Minimum metrics include latency percentiles, queue depth,
timeouts, route confusion, lane credibility, retrieval hit proxies, abstention/rejection
rates, model errors, and artifact generation.

### 43. No load, concurrency, soak, or failure-injection tests exist

The current tests are hermetic and fast. They do not prove memory limits, simultaneous
requests, simultaneous rebuilds, process restart, corrupt indexes, disk-full behavior,
model timeout, malformed model JSON at scale, or graceful cancellation.

### 44. No backup, rollback, or disaster-recovery procedure exists

Generated artifacts are reproducible only when private PDFs, RTL, exact model revisions, and
toolchain remain available. Define source backup, artifact retention, restore time, rebuild
time, and rollback procedure.

### 45. No deployment resource budget exists

The current virtual environment is about 5.6 GB with 146 installed packages; generated
output is about 56 MB before model weights. GPU/CPU RAM, VRAM, disk, cold start, throughput,
and concurrency targets are undocumented and ungated.

## Repository and dependency gaps

### 46. The repository snapshot is not reproducible

requirements.txt uses broad lower bounds for most dependencies and has no lockfile, hashes,
Python version, CUDA/toolchain version, or model revision pins. The installed environment
already contains versions far beyond the stated minimums. Rebuilding later can change APIs,
weights, numerical results, serialization, and security posture.

### 47. CI is absent

No tracked workflow runs tests, lint, retrieval smoke checks, artifact-schema validation,
dependency audit, or release evaluation. A local pass can regress immediately after merge.

### 48. Important repository files are ignored or untracked

The current .gitignore ignores all Markdown, all evaluation files by default, the source
PDFs, generated artifacts, and .gitignore itself. Only two evaluation files and selected
docs are re-included locally. The main tracked snapshot lacks the explanatory docs,
development question sets, gold answers, and several new safety/release modules and tests.

Private corpora may remain external, but their immutable identifiers/checksums and a
documented provisioning process must be tracked. Evaluation labels needed for release must
be versioned.

### 49. .gitignore ignores itself and contains duplicate/noisy entries

A self-ignored .gitignore cannot be added normally, so ignore policy can vanish in a clean
checkout. The file also contains a duplicate .gitignore line. Track one clean ignore file.

### 50. Packaging and entrypoints are absent

src is a script directory rather than an installable package. Imports depend on placing src
on sys.path by executing a script there. There is no pyproject, console entrypoint, version,
build artifact, or compatibility policy.

### 51. Automated style/static checks are narrow

The code-quality test enforces docstrings and comment shape, but there is no formatter,
linter, type checker, dead-code check, import check, complexity budget, or coverage report.
The current full diff also has a whitespace warning in test_bridge.py.

### 52. Dependency security and licenses are not gated

No lock, software bill of materials, vulnerability scan, model license inventory, package
license review, or signature/checksum verification exists. This audit does not claim that
the current packages are vulnerable; it identifies the missing gate.

### 53. Documentation is inconsistent and mostly outside version control

The old architecture material described route-specific retrieval even though the current
graph always runs both lanes. The Socratic guide was corrected during this audit, but the
other ignored documents can still drift and will not reach a clean deployment unless they
are tracked and reviewed together.

### 54. Global naming configuration prevents safe multi-corpus isolation

configure.py mutates a process-global abbreviation map during import. That is acceptable for
one corpus per process. A multi-tenant or multi-corpus server can leak one corpus's vocabulary
into another unless configuration becomes instance-scoped.

### 55. Benchmark reporting contains a misleading empty-slice default

bench._mean defaults an empty metric to 1.0. retrieval_metrics uses it for
dual_evidence_recall_at_k even in SPEC-only and RTL-only slices, so those reports display a
perfect dual-domain value where the metric is inapplicable. Empty metrics should be null or
omitted.

### 56. Release invalid-citation counting is narrower than validation

release_gate increments invalid_citations only for the literal invalid_citation error.
quote_not_in_evidence, missing_domain_citations, invalid_quote, and ambiguous/truncated
source failures still reject the answer, but the displayed invalid-citation metric can
remain zero. Report every provenance-validation category separately.

## Recommended completion order

1. Decide and document the deployment target and product promise.
2. Fix RTL warnings or establish reviewed, versioned waivers.
3. Version the private corpus by checksums and track all evaluation/release assets.
4. Pin Python, Verilator, dependencies, model revisions, and index-format identity.
5. Build versioned immutable artifact snapshots and validate them before atomic promotion.
6. Remove unsafe artifact loading or enforce signed read-only artifacts.
7. Share model instances and add bounded input, concurrency, cancellation, and timeouts.
8. Fix route intent, retrieval quality, and generated RTL citation semantics.
9. Expand the independent held-out suite and approve exact thresholds.
10. Run a fresh answer evaluation, complete hash-bound human review, and make the gate pass.
11. Add the service boundary, authentication, redacted observability, health checks, and CI.
12. Load-test, fault-test, deploy to a staging environment, and practice rollback.

Production-ready means every blocking item above has an owner, acceptance criterion, and
passing evidence in the exact release artifact.
