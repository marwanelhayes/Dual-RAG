# Spec: `src/spec_signals.py`

Decided 2026-09-23 in the simplification pass (remove-first, pipeline order, no knowledge of
later stages used to justify a function). Deferred options: [`docs/DEFERRED.md`](../DEFERRED.md)
DF-04 direction, DF-05 instructions, DF-06 register field layouts, DF-07 `row_kind`.

## Domain

A **spec signal** is a named wire or register a design must carry, as stated in a
specification table: an interface port (`AWVALID`) or a CSR (`mstatus`). Instruction
mnemonics, bit slices (`ACADDR[51]`), sub-register fields (`vma`), and parameters
(`LOOP_W_WIDTH`) are not spec signals.

One record is one **mention**: one table row. The same signal stated in three tables is
three records with three provenances. Grouping by `(document_id, normalized_name)` is the
consumer's one-liner.

## What spec_signals must do

1. **Read table chunks only** from `chunks.jsonl`. Prose is not read.
2. **Admit a table** when it has an explicit signal/port column, or a `name` column plus a
   width, source, or address column. Captions play no part.
3. **Name cell** must be a plain identifier `^[A-Za-z_$][A-Za-z0-9_$]*$` and not a header
   word. Suffixes (`[n]`, `[hi:lo]`, `.x`) reject the row.
4. **Address tables**: a row needs a non-empty address cell (drops group-label rows such as
   `Supe` / `rvisor Trap Setup` split across cells).
5. **Stated facts only**, stored as the raw cell: `width` from a Width column, `source` from a
   Source column, `description` from a Description column. No prose fill, no normalization
   maps. `bits` is a bit position, not a width.
6. **Identity**: `spec::signal::<document_id>::<table_label>::<normalized_name>`, with
   `::<n>` for the n-th occurrence of that name in the table. One counter spans every chunk
   of a windowed table, so IDs are unique by construction. `row_index` is table-global
   (`row_start` + position).
7. **Contract**: ingest guarantees `document_id`, `table_id`, `table_label`, `page`,
   `source_file`, `caption`, `columns`, `rows`, `row_start` on table chunks. Read them with
   `record["key"]`; a missing key is an ingest bug and fails loudly.
8. **Write** `{"schema_version", "signals"}` atomically (`.tmp` + `replace`).

Header synonyms (`signal name`, `port name`, `driven by`, `size`, ...) are kept as data: they
cost no logic and match common vendor conventions.

## Record contract (target)

`signal_id`, `name`, `normalized_name`, `normalized_tokens`, `width`, `source`,
`description`, `document_id`, `table_id`, `table_label`, `table_caption`, `row_index`,
`page`, `source_file`, `chunk_id`.

Removed now (no reader): `signal_name`, `content_hash`, `width_source`, `direction_source`.

Pending consolidation (decided, done at the `bridge` / `agent` walk, consumers switched in
the same step): `id`, `original_name` (→ `name`); `row_kind`; `direction`; `source_role` and
`source_role_source` (→ raw `source`); `width` becomes the raw cell (bridge compares ints
today); `section_id`, `section_title`, `subsection_id`, `subsection_title` (→ ingest's
`hierarchy_id` + `section_path`).

## Metrics (`track.py`, prefix `signals.`)

| Metric | Expectation |
|---|---|
| `rows`, `unique_names` | 1100 rows at `ingest/caption-regex`; about 486 / 326 at the end |
| `<document_id>.rows`, `<document_id>.unique_names` | per-document effect of each step |
| `tables_admitted` | falls with the caption rule and instruction tables |
| `with_width`, `with_source`, `with_description` | fill rates of stated facts |
| `axi_channel_recall` | names in frozen `eval/axi_channel_signals.json` (AXI B1.1-B1.7) found; stays 1.0 |

## Execution queue

Every step: failing test first, full suite green, commit, `build.py --clean`,
`bench.py --retrieval`, `track.py --tag <tag>`.

| Step | Tag | Change | When | Expected diff |
|---|---|---|---|---|
| s0 | `signals/metrics` | metrics above; freeze `eval/axi_channel_signals.json`, un-ignore it | now | metrics only |
| s1 | `signals/drop-dead-fields` | drop the 4 unread fields, contract fallbacks; move naming test to `test_naming_suffixes.py`; schema 5 | now | keys removed |
| s2 | `signals/drop-instructions` | remove instruction headers, `_MNEMONIC`, `instructions_from_prose` | after ingest queue | -441 rows |
| s3 | `signals/plain-identifier` | plain identifier regex | after ingest queue | -97 rows |
| s4 | `signals/address-required` | address tables need an address cell | after ingest queue | -19 rows |
| s5 | `signals/drop-caption-rule` | remove `_SIGNAL_CAPTION` | after ingest queue | -5 rows |
| s6 | `signals/stated-width-only` | remove `prose_widths`; `bits` out of width headers | after ingest queue | -25 prose widths, -29 rows |
| s7 | `signals/table-identity` | one occurrence counter across windows; drop `seen`; table-global `row_index` | after ingest queue | 0 rows lost |
| - | consolidation | pending list above | bridge / agent walk | consumers switch |

Behaviour steps s2-s7 wait for the ingest queue so every row delta is attributable to one
change.
