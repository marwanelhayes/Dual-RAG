# Specification signal inventory contract

`src/spec_signals.py` reads structured table chunks, not arbitrary prose. It accepts
explicit signal/port columns, or qualified Name tables beside width, source or address
columns. Original cell values and source provenance survive normalization. Address
rows without addresses are group labels, not registers.

IDs distinguish repeated occurrences across table windows. The inventory supplies
identity navigation to the bridge; it is not a complete requirement, instruction,
register-field or temporal-property model. No prose extraction, fabricated directions
or inferred widths are claimed. `spec_signals.json` is part of the sealed generation.
