# Contributing

Thanks for helping improve the Compliance Evaluator. Keep changes focused,
document behavior that users or operators need to know, and add tests for
correctness or security-sensitive changes.

## Development setup

Requirements: Python 3.12+, `uv`, and (for container workflows) Podman.

```bash
uv sync --extra dev
uv run pytest tests/unit -q
```

The default test suite does not need Elasticsearch or Ollama. Integration
tests require a local stack and are enabled explicitly:

```bash
COMPLIANCE_IT=1 uv run pytest tests/integration -q
```

For the local stack and end-to-end demo, follow the Quick Start in
[README.md](README.md).

## Change guidelines

- Preserve the code-owned verdict and citation-validation contract.
- Treat uploaded documents and model output as untrusted input.
- Never log raw document text, credentials, or prompt contents.
- Keep settings externalized and validate invalid combinations at startup.
- Surface infrastructure failures as operational errors; do not turn a failed
  dependency call into a substantive compliance verdict.
- Keep API and index changes backward-compatible where possible. Document any
  migration or behavior change.
- Avoid adding runtime dependencies for functionality available in the
  standard library or existing dependencies.

## Pull requests

1. Describe the user-visible behavior and any trade-offs.
2. Add or update unit tests; do not require live services for deterministic
   behavior.
3. Run `make test`, `make lint`, and `git diff --check`.
4. Update the README or architecture documentation when operational behavior,
   configuration, API contracts, or diagrams change.

Do not include real policy documents, secrets, generated build output, or
unrelated formatting changes.
