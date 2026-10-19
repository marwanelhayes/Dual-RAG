# Domain context

- **Specification:** a versioned hardware document. Its hierarchy and tables are
  evidence sources; retrieval chunks carry source file and PDF page provenance.
- **RTL:** Verilog/SystemVerilog source elaborated by Verilator. Source modules,
  declarations, instances and constructs retain compiler-derived locations.
- **Search surface:** text optimized for retrieval. Expanded identifiers and
  compiler-derived summaries are useful search features, not verbatim source quotes.
- **Source excerpt:** original RTL text with its physical file and line span, or
  specification text with document/page provenance. Only visible source excerpts
  authorize answer citations.
- **Bridge:** identity/navigation hypotheses between specification entities and
  RTL entities. Identity never establishes behavior or correctness.
- **Generation:** a complete, sealed corpus and compatible specification vector
  index. `output/current` selects the generation for new processes. Running readers
  retain the generation resolved at startup.
- **Development benchmark:** versioned labels used for tuning. Retrieval recall,
  source containment, and answer quality are distinct measurements.
- **Held-out candidate:** unapproved evaluation data. It becomes a release holdout
  only after independent review, threshold approval and freezing.

The public contract is evidence assistance. Behavioral verification requires
independent engineering review and appropriate simulation or formal checking.
