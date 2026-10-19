# Repository working instructions

Read [AGENTS.md](AGENTS.md), [CONTEXT.md](CONTEXT.md) and the relevant current stage
contract under `docs/specs/` before changing behavior. Follow [docs/CODE_STYLE.md](docs/CODE_STYLE.md).

Use deterministic reproductions and regression checks for nontrivial changes. Keep
source provenance and validation intact. Never mutate a published corpus generation;
use a complete build or explicit scratch output. Do not infer answer quality from
unit-test passes or behavioral correctness from retrieval/identity matches.

Current documentation is linked from [README.md](README.md). Documents under
`docs/history/` are historical evidence, not operating instructions. Do not require
or regenerate ignored code-graph artifacts as part of routine changes.
