# Specification ingestion contract

`src/ingest.py` is the active PDF backend. It reads text-layer hardware specifications,
using outline destination points or typographic heading recovery, and strips repeated
page furniture by position. It preserves section hierarchy and PDF page provenance.
Normalized source IDs must be unique; conflicting filenames fail before publication.

Tables are captioned structured rows, including recognized page-break continuations.
Their regions are excluded from prose. Sentence windows remain within sections; table
windows keep whole rows and repeat column headers. Breadcrumb plus body must fit the
configured encoder limit, measured with the pinned encoder tokenizer. Unknown revisions
are metadata, not an inferred version.

`section_nodes` and `section_records` are shared with the optional Markdown experiment.
The CLI writes `spec_hierarchy.json` and `chunks.jsonl` only into writable staging/scratch
output. A complete build publishes them as part of one sealed generation.

No active support is claimed for scanned pages, figure/waveform evidence or behavioral
interpretation. See [the OCR experiment](ocr_ingest.md), [pipeline operations](../../PIPELINE.md),
and [historical ingestion measurements](../history/pre-remediation/specs/ingest.md).
