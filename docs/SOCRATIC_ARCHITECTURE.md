# Questions for understanding ChipVer

- Where is the evidence from? PDF text/tables retain page provenance; RTL structure
  and original source excerpts come through Verilator and source locations.
- What can be generated? Search summaries and identity hypotheses. Neither is a
  verbatim source quotation or a behavioral verdict.
- Why two search lanes? Specification prose and RTL names have different retrieval
  needs. Every question searches both; intent constrains answer-domain requirements.
- What prevents stale mixtures? Complete builds publish one verified generation;
  readers pin it once. Index compatibility and bridge fingerprints are checked.
- What fits the model? Whole source passages measured with the pinned answer
  tokenizer, with explicit room for output. Removed passages cannot be cited.
- What does validation establish? Schema, source locations, exact quote containment
  and required-domain coverage. It does not establish relevance or completeness.
- What establishes release quality? Independently reviewed held-out labels and
  answers, approved thresholds, and evidence from the actual release environment.
- What establishes hardware correctness? Appropriate simulation/formal properties
  and engineering review; the current system supplies evidence assistance.

See [the architecture](ARCHITECTURE.md) and [pipeline guide](../PIPELINE.md).
