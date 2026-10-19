# RTL ingestion contract

`src/rtl_ingest.py` runs Verilator lint/XML extraction and projects modules, scoped
declarations, types, enum members, bindings, reader/writer sites and useful constructs.
Original source spellings, source text and physical relative file paths are evidence.
Parameterized source clones are consolidated for source navigation; this is not a
cycle-accurate simulator or a substitute for configuration-specific verification.

Recursive source discovery finds an unambiguous top, or `RTL_TOP_MODULE` supplies it.
`RTL_PROJECT` can select an explicit JSON manifest with ordered `sources`, `top`,
`include_dirs`, `defines` and `parameters`. Paths must resolve inside `rtl/`.
Compiler-provided subdirectories are retained, including duplicate basenames.
Manifest source ordering is authoritative; discovery alone does not promise package
import dependency ordering.

Compiler errors or nonzero exits fail the stage. Warnings are retained as diagnostics
of the input design, without asserting design correctness. `rtl.json` is published
only within a complete validated generation. See [the manifest example](../../PIPELINE.md).
