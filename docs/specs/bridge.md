# Cross-domain bridge contract

`src/bridge.py` links table-derived specification identities to compiler-derived RTL
signals and enum members. Exact/normalized identity links are STRONG; supported
interface-prefix links are MEDIUM. A table/enum address map is usable after the
configured minimum count of matching names and addresses confirms that map.

Widths remain stated evidence, not proof of equivalence. Similar names can appear
as unresolved suggestions; they are not accepted identity links. Section/module
alignment uses title identity and supported child identities. No semantic reranker,
local-model resolver or behavioral verifier exists in this stage.

`bridge.json` records input artifact fingerprints and is atomically written in the
staged generation. Runtime checks fingerprints before caching; failed loads remain
failed on subsequent calls. A bridge match means where to inspect, never implemented,
missing, correct or complete behavior.
