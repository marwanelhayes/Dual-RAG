> Historical record archived on 2026-10-19. Commands, metrics, and descriptions below are not the current contract. See [current architecture](../../ARCHITECTURE.md).

# Simplification method

How the `simplify` branch walks the pipeline. Use this to start the next session on the
next file.

## The prompt that starts a walk

Invoke `/grill-me` with the file to walk. The prompt that started the 2026-09-23 session:

> this session is going to be a simplification lesson going on each file for file and going
> through the tests for this file there are two main goals for this project at this stage
> first of something that should have been added from the beggining which is mlflow tracking
> for where we are and documenting progress and versions step by step and the second is
> going through each file line by line understanding the basic specs and what each file does
> why the function is there and should it be added removed or not and how does it fit in the
> grand schemes of things this is going to be a remove first approach not an add first
> approach meaning we look at functions from the beggining of the pipeline without taking a
> look at the end and seeing why these functions are needed if not needed with no knowledge
> of the end of the pipeline then we ought to remove them and then add them in the future but
> to do this we have to go through each file in the order of the pipeline making our next
> step extracting signals from documents so that we are able to link them together with rtl
> signals and modules going for spec_signals.py

For the next file, keep the prompt and change the last sentence to name it, e.g.
"... making our next step building the spec index, going for embed.py".

## Principles

1. **Pipeline order.** Files are walked in `build.py` stage order: `ingest.py`,
   `spec_signals.py`, `embed.py`, `rtl_ingest.py`, `rtl_embed.py`, `bridge.py`, then
   `retrieve.py` / `agent.py`.
2. **Remove first.** A function, field, or rule stays only if the file's own job needs it,
   judged without knowledge of later stages. Anything justified only by a consumer is
   removed and logged in `docs/DEFERRED.md` with the condition that reopens it.
3. **Stated facts, not inference.** Extracted values are stored as the source states them
   (raw cells), not normalized through maps that can be wrong (for example `Tx -> manager`).
4. **Data before decisions.** Every question is grounded in the real corpus output: counts,
   samples, and what each rule admits or drops.
5. **Consumers switch at their own walk.** A field that a later file reads is not removed
   early; it goes on the file spec's "pending consolidation" list and is removed when that
   consumer is walked, in the same step.

## Question format

One question at a time, each with:

- the finding from the code or data (numbers, examples, line references);
- 2-3 lettered options, the recommended one first and marked **(A) Recommended**;
- the trade-off in one line per option;
- a closing "A?" so the answer can be one letter.

Questions that code or data can answer are answered by looking, not asked. Decisions
already made are not re-asked. When a user answer exposes an inconsistency with an
earlier decision (Q12 synonyms vs Q7 direction), raise it as its own question.

## Output of a walk

- `docs/specs/<file>.md`: domain, rules, record contract, metrics, pending consolidation,
  and an execution queue of small steps with tags and expected diffs.
- `docs/DEFERRED.md`: one `DF-nn` entry per removed-but-plausible option.
- `docs/NEXT_STEPS.md`: where we are, run IDs, the next steps.

## Executing a step

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests   # failing test first, then green
git commit ...                                             # one step per commit, tag in subject
.venv/bin/python src/build.py --clean                      # ~3 min, needs the GPU
.venv/bin/python src/bench.py --retrieval
.venv/bin/python src/track.py --tag <step tag>
```

- **TDD.** Write the failing test first. For refactors with no test, pin behaviour with a
  characterization test that is green before and after. Hermetic PDFs are generated with
  PyMuPDF in `tmp_path` (see `_tiny_pdf` in `tests/test_spec_ingest.py`).
- **Zero-diff proof.** For removal steps, snapshot `output/chunks.jsonl` before the build
  and prove the new output equals the old minus the removed keys.
- **Clean tree.** Run build, bench, and track in the foreground. Never edit while they run:
  `track.py` records `git_dirty`, and a dirty run is re-logged via `git stash`.
- **Decision metric.** `retrieval.spec.section_recall_at_k` (gold labelled by section, so
  re-chunking does not break it). Chunk-ID metrics are logged, never decisive.
- **A drop on the decision metric stops the queue** until the per-question diff (from the
  previous run's `retrieval_bench.json` MLflow artifact) explains it.
