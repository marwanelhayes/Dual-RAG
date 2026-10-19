# Change and evaluation discipline

Read [CONTEXT.md](../CONTEXT.md) and the relevant current stage contract before editing.
Reproduce a reported failure with a deterministic check, add a regression at its real
boundary, then make the smallest complete fix and run the affected tests.

Use isolated corpus generations for builds and preserve the active generation when
any check fails. Record source, tool, dependency and model identities before comparing
experiments. Keep download time, cold inference and cached parsing measurements separate.

Evaluate retrieval independently from answer quality. Section recall survives
specification re-chunking; exact chunk IDs are diagnostic. A live smoke test checks
integration, not accuracy. Only independently reviewed held-out answers can support
a release-quality claim. Historical simplification outcomes are
[archived](history/pre-remediation/SIMPLIFICATION_METHOD.md).
