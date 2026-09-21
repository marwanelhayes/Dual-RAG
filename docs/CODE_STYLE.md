# ChipVer commenting style

This guide supersedes the earlier didactic commenting convention referenced by historical
plans. Runtime code should stay compact; durable design history belongs in
`ARCHITECTURE_DECISIONS.md`.

## Rules

1. Give every module, class, function, method, and nested function a concise descriptive
   docstring. Add arguments, returns, invariants, or error behavior only when the interface
   is not already clear from the definition.
2. Use a comment only for a non-obvious local reason, invariant, ordering constraint, unit,
   or external-system quirk. Prefer one accurate line immediately above the relevant code.
3. Do not narrate syntax, repeat names, preserve experiment logs inline, or restate the
   docstring.
4. Do not use unassigned triple-quoted strings as comments. They execute as useless string
   expressions and obscure the implementation.
5. Keep measured history and rejected alternatives in architecture or experiment notes;
   leave only the conclusion required to edit the current code safely.
6. Preserve type-checker and linter directives such as `# type: ignore` or `# noqa` when
   they have tool behavior.

## Example

```python
def reciprocal_rank_fusion(rankings, damping):
    """Fuse ranked identifier lists without comparing incompatible raw scores."""
    scores = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            # Rank is the only scale shared by lexical and dense retrieval.
            scores[item_id] = scores.get(item_id, 0.0) + 1 / (damping + rank)
    return sorted(scores, key=scores.get, reverse=True)
```
