# Deferred capabilities and validation

The detailed historical deferrals are [archived](history/pre-remediation/DEFERRED.md).
Current deliberate limits are:

- Independent held-out labeling and answer review require a human domain expert.
- Behavioral conformance requires simulation/formal integration and reviewed properties.
- Figures, waveforms and scanned documents are outside the active PDF backend.
- Qianfan-OCR remains an optional experiment; its full comparison has not completed.
- Authentication, tenant isolation, distributed serving and multiple GPU workers
  require a deployment scope beyond the trusted-local service.
- Incremental embedding, query recovery, semantic bridge reranking and hierarchy
  expansion are not implemented. Add them only after measured evidence supports them.
- The dependency lock is the validated Linux CUDA environment, not a universal lock.

Known scope limits must remain visible in reports; they are not evidence of a passed
release gate or permission to make unsupported product claims.
