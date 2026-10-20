# Current architecture decisions

1. Keep specification and RTL search independent. Their retrieval units and ranking
   strategies differ; they share the cross-encoder, not a competing corpus.
2. Trust compiler structure for RTL. Preserve original names, relative source paths,
   source text and physical locations. Explicit project manifests override discovery.
3. Preserve specification section boundaries and raw table cells. Keep page provenance;
   split long tables by whole rows and fail on encoder overflow.
4. Separate search surfaces from citable source text. Generated summaries cannot
   authorize quotations attributed to original RTL lines.
5. Treat bridge links as identity hypotheses. Width evidence and name similarity
   are navigation information, never implementation proof or proof of absence.
6. Keep the two-lane linear graph as the baseline. The opt-in
   [Kev experiment](KEV_RETRIEVAL_EXPERIMENT.md) tests conditional domain retrieval;
   comparisons and router fallbacks still require both domains.
7. Budget actual answer-model chat tokens, retain whole passages and reserve output
   capacity. Return validated source excerpts, with unverified comparison labeling.
8. Publish complete immutable generations after validation. Pin readers to one
   generation; reject incompatible indexes and cache only validated artifacts.
9. Serialize local GPU inference and validate readiness before traffic. The service
   is trusted-local and uses one worker per GPU. Release unused retrieval CUDA cache
   before generation and wait for the answer model to unload afterward so both stages fit.
10. Pin model revisions and record source/tool/package provenance. Version evaluation
    labels and require independent approval for release quality claims.

The earlier decision sequence, superseded contracts and measured experiments are
preserved in [the historical archive](history/pre-remediation/ARCHITECTURE_DECISIONS.md).
