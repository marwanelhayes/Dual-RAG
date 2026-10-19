> Historical record archived on 2026-10-19. Commands, metrics, and descriptions below are not the current contract. See [current architecture](../../../ARCHITECTURE.md).

# Spec: `src/rtl_ingest.py`

Decided 2026-09-23 in the simplification pass. Deferred options live in
[`docs/DEFERRED.md`](../DEFERRED.md) (DF-10 elaborated clones, DF-11 clock/reset roles).

## Domain

`rtl_ingest.py` projects Verilator's elaborated AST (`--xml-only`) plus its lint output into
`output/rtl.json`: one record per source module with its declarations, structural blocks,
instances and port bindings, the elaborated instance tree, package enums, and lint warnings.
SystemVerilog is never parsed a second time; every fact comes from the compiler or is a
citation of source lines.

Non-goals: retrieval records and chunking (`rtl_embed.py`), spec linking (`bridge.py`),
identifier normalization rules (`naming.py`, judged at the bridge walk).

## What rtl_ingest must do

1. **Run Verilator** lint-only, then XML-only; any `%Error` stops before writing.
2. **Modules** keyed by source name (parameter clones collapse through `source_name`), with
   file, line extent, and source.
3. **Declarations** (ports, signals, parameters): name, kind, direction, data type, width,
   width expression, value, parameter kind, lexical `hierarchical_path`, `signal_id`, source
   citation, reader and writer sites.
4. **Blocks** (always, functions/tasks, initial, grouped continuous assignments): construct
   type, source, signals read and written, `sensitivity` (signal + edge as stated).
5. **Instances and bindings**: target module, lexical path, and per port the formal
   (`formal_name`, `formal_signal_id`), the actuals (`actual_signals`, `actual_signal_ids`),
   direction, expression kind, and the source line.
6. **Tree**: the elaborated instance hierarchy (depth, instance, module, path); the only home
   of elaborated occurrence paths.
7. **Enums** with member values (`value` parsed, `value_source` as written) and, until the
   bridge walk, `usage_sites`.
8. **Warnings**: Verilator lint as `{code, file, line, message}`.

## Removed

| Item | Why |
|---|---|
| binding `formal`, `actual_refs`, `formal_symbol_id`, `actual_symbol_ids` | unread duplicates of kept names |
| instance `target_elaborated_module` | unread duplicate of the list form |
| `dtype_id`, `elaborated_modules`, `target_elaborated_modules`, tree `elaborated_module` / `loc`, binding `port_index` | Verilator internals, not design facts |
| instance / binding `hierarchical_paths`, binding `hierarchical_path` | copies of `tree` (731 per-binding copies) |
| `parent_construct` | 7 of 1642 point to generate-scope ids no record carries |
| binding `instance_id`, `instance`, `binding_id` | bindings are nested in their instance |
| tree `parent_hier` | `hierarchical_path[:-1]` |
| block `clock`, `reset` | name heuristic; misses `ARESETn`; `sensitivity` states the facts |
| every `content_hash` | only copied into unread chunk metadata; freshness is the embed manifest |

## Pending consolidation

- Alias pairs read by consumers: `file` / `file_path`, `line` / `start_line`, `symbol_name` /
  `original_name`, `instance_id` / `construct_id` (rtl_embed, bridge, agent walks).
- File paths become POSIX paths relative to `RTL_DIR` at the `rtl_embed.py` walk, together
  with its readers (`rtl_embed.py:309`, `:488`).
- `enum_usage` (regex over block source; 13 of 326 sites match only in comments) moves to
  or is dropped at the `bridge.py` walk.
- `rtl_embed` lines copying `clock`, `reset`, and `content_hash` into chunk metadata go at its
  walk.

## Metrics (`track.py`, prefix `rtl.`)

Counts: modules, ports, signals, parameters, instances, bindings, blocks per construct
type, enums, enum items, enum usage sites, tree nodes, warnings per code, `rtl_bytes`.
Rates (a fall means something broke): declarations with a width, bindings whose formal
resolves to a declared port, bindings whose actuals all resolve, signals with a writer.
Downstream guard: RTL retrieval section recall 1.0, MRR 0.584.

## Execution queue

Every step: failing test first, full suite green, commit, `build.py --clean`,
`bench.py --retrieval`, `track.py --tag <tag>`. Removal steps prove `rtl.json` new = old
minus the removed keys.

| Step | Tag | Change | Expected diff |
|---|---|---|---|
| r0 | `rtl/metrics` | `rtl.*` metrics in `track.py` | metrics only |
| r1 | `rtl/drop-aliases` | drop 5 alias fields; `SCHEMA_VERSION` 3 → 4 | minus keys |
| r2 | `rtl/drop-compiler-internals` | drop 6 Verilator-internal fields | minus keys |
| r3 | `rtl/hierarchy-once` | occurrence paths only in `tree`; drop dangling and nested ids | minus keys |
| r4 | `rtl/drop-clock-reset` | drop `clock`, `reset` | minus keys |
| r5 | `rtl/drop-content-hash` | drop all `content_hash`; no import from `ingest` | minus keys |
