# Spec: `src/agent.py`

Decided 2026-09-26 in the simplification pass. Deferred options live in
[`docs/DEFERRED.md`](../DEFERRED.md) (DF-26 to DF-32).

## Domain

`agent.py` answers one question from both evidence lanes: route by wording, retrieve spec and
RTL evidence, look up bridge alignments, assemble a bounded context of whole citable passages,
and ask the local model for source-quoted claims that `answer_contract.validate_answer`
checks.

Non-goals: retrieval ranking (`retrieve.py`), building artifacts (earlier stages), answer
validation rules (`answer_contract.py`, not walked).

## Baseline (2026-09-26, head `37237e0`, 60 bench questions)

- Retrieval@5 through the agent equals the stage retrievers (spec 0.683, RTL 0.500): the
  agent's additions all land after position 8.
- **Context recall** (gold inside `citable_evidence`, what the model sees): spec section
  0.575, RTL exact 0.308, mean 9.2k chars. Every gold passage in context came from a direct
  retriever hit.
- Hierarchy neighbours: 2 per question, 0 gold. Near-duplicate drop: 0 of 24 dropped.
- RTL blocks: 27 questions, 0 gold; without them RTL context recall 0.508.
- Reverse-bridge re-query: 10 questions, 0 gold. Recovery: off, 0 runs.
- Credibility gate: 7 abstentions, 1 correct.
- Budget ×2 / ×4: spec 0.731 / 0.756, RTL 0.583 / 0.592.
- `classify_query`: correct on every labelled set (60 + 60 + 10 + 7).
- Bridge text: present on 32/60; names a gold module on 5/20 comparison questions.

## What agent must do

1. **Graph (LangGraph, kept for learning)**: `classify → retrieve_spec → retrieve_rtl →
   lookup_bridge → merge → answer`, linear.
2. **classify**: `classify_query` regexes → `query_type`.
3. **retrieve_spec**: `Retriever.search(question, AGENT_SPEC_K)`; `select_spec_signals`
   (provenance by `chunk_id`, `table_ids`, `hierarchy_id`; name overlap) for the bridge only.
4. **retrieve_rtl**: `RTLRetriever.search(question, AGENT_RTL_K)` over `rtl_chunks.jsonl`.
5. **lookup_bridge**: signal alignments + unresolved rows by spec signal id/name; module
   alignments by `spec_section_id` ∈ evidence `hierarchy_id`; read `bridge.json` keys directly.
6. **merge**: whole passages, spec then bridge rows then RTL; no character truncation;
   `AGENT_NUM_CTX = 8192`.
7. **answer**: required domains (both for COMPARISON, else the routed one) must be citable,
   else abstain; model → `validate_answer` → `render_answer`.
8. **Freshness guards** kept: spec index, RTL index (from `rtl_chunks.jsonl`), bridge
   fingerprints.
9. **Hit type**: `(Document, score)` from both retrievers; `_hit_record` drops its bare-hit
   branch.

## Removed

| Item | Why |
|---|---|
| `expand_spec_hierarchy`, `get_hierarchy`, `drop_near_duplicates`, `_shingles`, over-fetch | DF-27 |
| `bridge_targets`, `surrounding_rtl_blocks`, `_staged_names`, `_staging_base`, `_STAGING_SUFFIX`, `align_block_ids`, `_module_table`, `_excerpt_block`, `rtl_blocks` | DF-28 |
| `reverse_bridge_terms`, re-query in `expand_node`, `spec_rankings` | DF-29 |
| `recovery_node`, `recovery_query`, `local_query`, `fuse_recovery`, `recovery_done`, `query_model`, `query_recovery`; bench `compare_recovery`, `query_llm_calls`, `query_errors`; `retrieve.reciprocal_rank_fusion`, `RETRIEVE_RRF_K` | DF-30 |
| `lane_strength`, `background_queries`, `background_floor`, `get_lane_floors`, `credible_lanes`, `lane_calibration.json`, credibility shares and gate, `expand` / `allocate` nodes | DF-31 |
| `STRUCTURED SPEC SIGNALS` rows | DF-32 |
| char budgets, share math, `_bounded` passage cuts, `[TRUNCATED]`, `citable_evidence`'s in-context check | whole passages |
| `observed_route`, `observed_query_type`, `absent_terms`, `ask`, callable-adapter branches, `get_rtl`, `rtl=` | 0 readers / test seams |
| bridge aliases (`spec_signal`, `spec_name`, `spec.name`, `rtl_signal`, `rtl_name`, `signal_name`, `rtl_module`, `module_name`, `spec_section`, `spec_title`, `confidence`, `status`, `component_evidence`) | `bridge.json` schema |
| config: `AGENT_SEARCH_K`, `AGENT_SPEC_OVERFETCH`, `AGENT_DEDUP_*`, `AGENT_RTL_BLOCK*`, `AGENT_LANE_SAMPLE`, `LANE_*`, `AGENT_WEAK_LANE_SHARE`, `AGENT_REVERSE_TERMS`, `AGENT_QUERY_RECOVERY`, `AGENT_ABSENCE_TERMS`, `AGENT_*_CONTEXT_CHARS`, `AGENT_MODEL`, `BRIDGE_QUERY_*` | readers removed |
| ingest `section_id`, `section_title`, `subsection_id`, `subsection_title`, `doc_name`; spec_signals `section_id`, `subsection_id` | agent reads `hierarchy_id` |

Kept although planned otherwise: `langchain_core.Document` hit type (Q12; `langgraph` needs
`langchain-core` anyway).

## Metrics (`track.py`)

Decision: `retrieval.context.spec_section_recall`, `retrieval.context.rtl_exact_recall` (gold in
`citable_evidence`). Guards: `retrieval.context.chars`, `retrieval.*`, `retrieve.*`. The answer
bench (Ollama) is not run in this walk.

## Execution queue

Every step: failing test first, full suite green, commit, bench, track. Rebuild only when a
stage artifact changes (a12).

| Step | Tag | Change | Expected diff |
|---|---|---|---|
| a0 | `agent/metrics` | context recall + chars in bench, logged by track | metrics only |
| a1 | `agent/drop-recovery` | Q5 | zero |
| a2 | `agent/drop-dead-code` | dead state, `ask`, adapters, dedup + over-fetch, config fallbacks | zero |
| a3 | `agent/drop-hierarchy-expansion` | Q2 | context |
| a4 | `agent/drop-rtl-blocks` | Q3 | RTL context ~0.31 → ~0.51 |
| a5 | `agent/drop-reverse-requery` | Q4 | context |
| a6 | `agent/drop-lane-floors` | Q7 + linear graph (Q11) | abstentions |
| a7 | `agent/whole-passages` | Q8, `AGENT_NUM_CTX` 8192 | context recall up |
| a8 | `agent/drop-signal-rows` | Q10 | context |
| a9 | `sweep/agent_k` | k ∈ {5, 8} per lane | pick by context recall, chars guard |
| a10 | `agent/bridge-schema-keys` | Q6, `hierarchy_id` | zero |
| a11 | `agent/rtl-chunks` | records from `rtl_chunks.jsonl`, freshness on them | zero |
| a12 | `ingest/drop-agent-section-fields` | ingest + spec_signals minus keys | minus keys |
