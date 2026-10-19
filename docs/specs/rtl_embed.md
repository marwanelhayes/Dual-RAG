# RTL retrieval record contract

`src/rtl_embed.py` writes `rtl_chunks.jsonl`; despite its historical filename it
performs no dense embedding. Module search text contains names, port relationships,
expanded terms and comments. Structural search text adds source code and reader/writer
terms. Long search surfaces are character-windowed, retaining stable base IDs and
`:wN` continuation IDs.

Every generated record additionally carries `source_excerpt`: original source text
and its physical start/end line. Source passages preserve complete lines. Search-window
position selects a bounded source window; it does not make generated summary text
citable. Long single source lines remain whole and may be removed by answer budgeting.

The agent hydrates RTL answers from these source excerpts, preserving full relative
paths. Search summaries and expanded terms never authorize source quotations.
Modules/signals can still be located by exact compiler metadata; useful constructs
supply behavioral source passages. Rebuild older records before serving.
