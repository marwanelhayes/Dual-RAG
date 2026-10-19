# Project summary

ChipVer is a local evidence assistant for hardware specifications and RTL. It does
not issue verified implementation verdicts. The authoritative operating guide is
[PIPELINE.md](PIPELINE.md); the current system design is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

The source pipeline preserves PDF hierarchy and structured tables, uses Verilator
for HDL structure, and builds independent specification and RTL search lanes. A
conservative offline bridge provides identity hints. The answer model selects
source excerpts; deterministic validation enforces citation and quote containment.

Current engineering work and remaining external validation are recorded in
[ENGINEERING_REMEDIATION.md](docs/ENGINEERING_REMEDIATION.md). Historical results are
archived under `docs/history/pre-remediation/` and are not current product contracts.
The Qianfan-OCR experiment remains optional and unpromoted.
