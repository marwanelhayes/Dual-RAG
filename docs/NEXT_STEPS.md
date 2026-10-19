# Current next steps

Use [the remediation record](ENGINEERING_REMEDIATION.md) for completed engineering
checks and unresolved external validation. Prior experiment queues are
[archived](history/pre-remediation/NEXT_STEPS.md).

1. Provision and fingerprint the intended corpus, then build a full generation.
2. Run contract tests and opt-in real-model smoke checks in the exact environment.
3. Have an independent domain expert review/expand and freeze held-out labels.
4. Generate real answers, review them against the frozen facts, and run the release gate.
5. Evaluate optional OCR on representative pages before a complete corpus comparison.

Do not tune against held-out answers or label a retrieval benchmark as answer accuracy.
