# Agent and answer contract

`src/agent.py` runs one linear graph for every question: classify, specification
retrieval, RTL retrieval, bridge lookup, evidence merge and answer. The deterministic
classifier preserves requested SPEC, RTL or COMPARISON intent. Both lanes run; intent
determines required citable answer domains. There are no evidence floors, recovery
queries, hierarchy expansion, or model-selected retrieval tools.

The tagger selects supported signal/register names for bridge navigation. Generated
RTL search text is replaced by original-source excerpts before prompt construction.
Full relative file paths and known pages/lines authorize citation labels. Conflicting
passages sharing a label are excluded; identical source passages are deduplicated.

The merge measures the actual Qwen chat template using the pinned tokenizer and
removes whole lowest-priority passages to reserve output capacity. The local request
also checks the budget, disables thinking and caps generated tokens. Its JSON
schema limits generation to three labels from the visible catalog. The application
copies their complete original passages (up to the validator's 2,000-character
quote limit), so the model never retypes source quotes. Provider-reported truncation
is rejected explicitly. Quotes are
validated against the exact visible catalog, never against hidden or removed passages.

`answer_contract.py` accepts only evidence_found/insufficient_evidence JSON and bounded
citation/quote lists. It enforces source containment and required domains; comparisons
are labeled UNVERIFIED COMPARISON. Relevance, complete fact coverage and behavioral
truth are outside deterministic quote validation.

Default API startup validates the sealed generation, actual index, Ollama model digest,
retrieval adapters, real tagger and answer tokenizer before readiness. Runtime lazy
initialization is locked; service inference is bounded to one request. Custom test
factories explicitly own their own initialization. See [release evaluation](../RELEASE_READINESS.md).
