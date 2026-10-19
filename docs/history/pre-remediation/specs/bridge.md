> Historical record archived on 2026-10-19. Commands, metrics, and descriptions below are not the current contract. See [current architecture](../../../ARCHITECTURE.md).

# Spec: `src/bridge.py`

Decided 2026-09-24 in the simplification pass. Deferred options live in
[`docs/DEFERRED.md`](../DEFERRED.md) (DF-14 to DF-19).

## Domain

`bridge.py` links specification identities to RTL identities and writes `output/bridge.json`:
spec signal rows to RTL ports / signals / enum members, and spec sections to RTL modules.
It states how each link was found; it never decides whether RTL implements a requirement.

Non-goals: retrieval and query expansion (`agent.py`), identifier tokenisation rules
(`naming.py`), extraction of spec rows (`spec_signals.py`) or RTL facts (`rtl_ingest.py`).

## Baseline (2026-09-24, head `95bc569`)

- Inputs: 1100 spec rows (487 AXI ports, 441 instructions, 172 register rows), 1506 RTL
  signals, 230 enum members, 54 sections considered.
- `signal_alignments` 18, all WEAK, 9 distinct AXI names, all on pass-through modules
  (`fetch_stage` 12, `memory_stage` 6). Unresolved 641 (no candidate 400, width 130, below
  threshold 111). `enum_alignments` 43 (instructions). `module_alignments` 2 of 54.
- The RTL implements 25 AXI channel signals as `M`-prefixed ports (`MAWValid`, `MRRData`...)
  on `cache`, the stages, and both memories; 9 of 25 are linked (recall 0.36, precision 1.0).
- Cause: correct ports rank first, but the weighted mean with section/description/table
  jaccards (about 0 for AXI) drops a 0.94 name score under the 0.62 threshold; the hand-made
  namespace prefix list lacks `mr` (R channel); a real width deviation (`ARSIZE` 3 bits in the
  spec, `MARSize` 4 in RTL) is rejected instead of reported.

## What bridge must do

1. **Flat names.** Compare `naming` tokens joined, lowercase, direction suffix removed
   (`AWADDR` → `awaddr`, `MAWAddr` → `mawaddr`).
2. **Link tiers** (each row states its `method`):
   - `identity`: flat names equal (STRONG);
   - `interface_prefix`: RTL flat = prefix + spec flat, where the prefix is learned per
     module (at least 2 of the module's ports are that prefix + a spec name); a port takes the
     longest spec name it matches (MEDIUM);
3. **Targets:** RTL ports and signals for every tier; enum members for `identity` (CSRs are
   declared as `csr_index_t` members) and for `address`: once an enum and a spec address table
   agree on name and number for `BRIDGE_ADDRESS_MAP_MIN` (2) rows, a row whose address equals
   a member's value links to it under another name (MEDIUM). Every enum-member link states
   `address: {spec, rtl, status}` (`equal`, `conflict`, `symbolic`); ranges never link.
4. **Width is stated, never a filter:** `{spec, rtl, status}` with status `equal`,
   `conflict`, `symbolic` (raw cell not an integer) or `unstated`.
5. **Unresolved** spec rows keep up to 3 `SUGGESTED` candidates (fuzzy token overlap / edit
   similarity, with width), never counted as links.
6. **Sections → modules** only on stated evidence: `title_identity` (normalised title tokens
   equal the module's after depluralising and dropping `file`/`state`; STRONG) or
   `link_support` (modules whose ports the section's own rows link to, with counts; MEDIUM).
   Sections with neither are not written.
7. **Freshness:** `source_fingerprints` of the three inputs (the agent refuses a stale bridge).

Link row: `spec_signal_id`, `spec_original_name`, `rtl_kind` (`signal` | `enum_member`),
`rtl_signal_id`, `rtl_original_name`, `rtl_module_id`, `rtl_module_name`, `rtl_direction`,
enum fields for members, `method`, `prefix`, `width`, `confidence_level`, `provenance`.

## Removed

| Item | Why |
|---|---|
| tiny-model resolver, `--llm`, `ollama_resolver`, `BRIDGE_LLM_*`, `lookup_signal`, `lookup_module` | never run by the pipeline; lookups only in tests (DF-14) |
| schema alias fallbacks (`_first` chains, list shapes, legacy `nodes`, hash ids) | real inputs use one key each |
| `module_roles`, `_source_role` synonym map, spec `direction`, `direction` component | 0 alignments change; synonym map rejected at spec_signals walk (DF-15) |
| enum (instruction) alignment, `rtl_ingest` `enum_usage` | input removed by s2; no consumer (DF-16) |
| weighted structural score, section/description/table jaccards, fuzzy probes as evidence, `--rerank` cross-encoder, numeric thresholds, hand prefix list | replaced by stated tiers (DF-17, DF-18) |
| module candidate scoring, context jaccard, `BRIDGE_MODULE_*` | fuzzy title matches drove agent expansion (`PCIe` → `pc_adder`) (DF-19) |

## Metrics (`track.py`, prefix `bridge.`)

Decision: `bridge.axi_recall` against `eval/bridge_gold.json` (25 AXI channel signals → port
and driving module). Guard: `bridge.axi_precision` (AXI link rows whose port is the gold port
or its `D`/`I` alias). Counts: links per method and per `rtl_kind`, unresolved per reason,
module links per method. Downstream guard: `retrieval.spec.section_recall_at_k`,
`retrieval.rtl.exact_id_recall_at_k`.

## Execution queue

After ingest 10-15 and spec_signals s2-s7. Every step: failing test first, suite green,
commit, `build.py --clean`, `bench.py --retrieval`, `track.py --tag <tag>`.

| Step | Tag | Change | Expected diff |
|---|---|---|---|
| b0 | `bridge/metrics` | gold file, `bridge.*` metrics | metrics only |
| b1 | `bridge/drop-llm-resolver` | resolver, `--llm`, lookups | zero |
| b2 | `bridge/drop-aliases` | read real keys | zero |
| b3 | `bridge/drop-roles` | roles, spec direction, `direction` component | alignments same |
| b4 | `bridge/drop-enum-alignment` | enum alignment; `rtl_ingest` `enum_usage` | `rtl.json` minus key |
| b5 | `bridge/identity-links` | tiers `identity` + `interface_prefix`, row shape, width stated; agent switched | recall up |
| b6 | `bridge/same-net` | `same_net` tier | + renamed aliases |
| b7 | `bridge/suggestions` | `SUGGESTED` candidates on unresolved | unresolved rows |
| b8 | `bridge/register-links` | enum-member targets | + CSR links |
| b9 | `bridge/module-links` | title identity + link support; agent branch removed | module rows |
| b10 | `bridge/consolidate-signals` | spec_signals `hierarchy_id`, `section_path`, raw `width`; drops pending fields | renamed / minus keys |

## Outcome (2026-09-24)

| | b0 | b10 |
|---|---|---|
| `bridge.axi_recall` | 0.44 | **1.0** |
| `bridge.axi_precision` | 1.0 | 0.94 |
| links (identity / interface_prefix / same_net) | 13 scored | 606 (34 / 553 / 19) |
| CSR → enum-member links | 0 | 18 |
| width conflicts stated | hidden | 23 (`ARSIZE` / `AWSIZE` 3 vs 4) |
| section → module rows | 53 (2 accepted) | 136 (2 title, 134 link support) |

Deviation: `confidence` (numeric) is gone; the agent prints `method=` instead. Precision
losses are exact-name collisions (`WDATA`→`WData`, `VALID`→`valid`) and the 19 `same_net`
links that spread them; `same_net` is reopened in `NEXT_STEPS.md`.
