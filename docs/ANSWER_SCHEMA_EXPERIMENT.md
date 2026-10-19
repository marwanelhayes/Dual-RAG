# Native answer domain and intent corrections

Date: 2026-10-19. Provider: local Ollama 0.18.3, pinned `qwen3:4b`.

The ten-question native benchmark exposed a gap missed by the earlier two-question
SPEC/RTL smoke check: six responses omitted a domain required by their route.
Independent source validation rejected all six, so none became an accepted answer.
The raw development report remains in generation `7c575f4040a24e9d9d561ae7aee57ef3`
as `bench.json`. Its keyword diagnostic counted five correct, two unverified and
three abstained; these labels are not expert approval.

Three hypotheses were checked: insufficient schema constraints, no selectable
passage in a required domain, or unintended routing. All ten catalogs contained
four to eight selectable SPEC passages and eight selectable RTL passages. The
explicit AWVALID comparison correctly required both domains. Replaying that prompt
with the original schema again selected three SPEC labels and failed
`missing_domain_citations`.

The fix supplies the route's required domains to the native adapter. Its schema
allows either an empty abstention or one to three labels with a mandatory label
from each required domain. Source copying and independent validation remain in
place. If the 2,000-character passage filter removes every selectable passage in
a required domain, the adapter abstains before generation.

A first JSON Schema candidate combined `prefixItems` and `items`. It passed the
standard validator but the real provider again returned SPEC-only citations. The
provider's grammar implementation gives `items` precedence, so the retained schema
enumerates bounded tuple alternatives using only `prefixItems`. The same real
prompt then returned both domains and passed source validation. This is why the
opt-in live suite now includes the failing comparison, alongside SPEC and RTL
requests. [Ollama's structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs)
describes the API; the [underlying schema parser](https://github.com/ggml-org/llama.cpp/blob/master/common/json-schema.cpp)
documents this keyword precedence in `build_array`.

Regression checks exercise the actual graph-to-provider schema boundary, complete
domain coverage, optional third labels, empty abstention, the output bound and a
domain made unselectable by the quote-length filter. Provider replay reports remain
in the same local generation as `domain_schema_unsupported_items.json` and
`domain_schema_replay.json`; `domain_probe.json` holds the inspected catalogs.

This corrects structural response reliability. It does not prove that selected
passages are relevant, complete, or evidence of behavioral conformance. Independent
answer review remains a release requirement.

The complete schema-only rerun on generation `8fba5c6a96d04b4cac075195747c676e`
passed source/domain validation for all ten requests. Its keyword diagnostic was
five correct, one wrong and four unverified. The flagged case quoted an unrelated
MISA paragraph containing "not implemented" alongside a relevant RTL excerpt for
the question "is the hazard unit implemented or not". Passing validation therefore
did not establish relevance.

The classifier had treated this local presence question as standards conformance.
The correction recognizes narrowly worded presence questions only when their target
is a known local design identifier, allowing spaces in underscore-separated names.
Behavior, correctness and explicit specification relations retain comparison
intent; unknown names do not establish absence. The development question's
`expected_query_type` was corrected from COMPARISON to RTL. Its question, factual
expectations, forbidden claims and citation labels were retained. No held-out
labels were changed or approved. Generic-name regression cases prevent a
hazard-unit-specific exception.

## Final verification

The full rerun on generation `b51bba4f29eb4723855fa3d0f0cb0264` completed all ten
native requests. Every response returned evidence excerpts and passed independent
source/domain validation. The presence request returned only RTL citations. It ran
first to check that regression before the remaining requests; timings are not a
controlled before/after latency comparison.

| Development run | Responses passing source/domain validation | Keyword correct | Keyword wrong | Keyword unverified | Keyword abstained |
| --- | --- | --- | --- | --- | --- |
| Original schema | 4/10 | 5 | 0 | 2 | 3 |
| Domain schema only | 10/10 | 5 | 1 | 4 | 0 |
| Domain schema plus presence intent | 10/10 | 5 | 0 | 5 | 0 |

The five unverified keyword outcomes remain visible. This is not an independently
reviewed accuracy result. The final software suite passed 312 tests with one opt-in
live check skipped; the staged checkout without the private corpus passed 310,
with three corpus/live checks skipped. The full build retained identical corpus
artifacts and recorded source hashes matching the final code. Raw per-question
answers and validation results are in the final generation's `bench.json`.
