# Security Policy

## Supported versions

Security fixes are applied to the current `main` branch.

## Reporting a vulnerability

Please report vulnerabilities privately and do not open a public issue.

Send details to the maintainers with:

- Impact summary
- Reproduction steps
- Affected files/components
- Suggested mitigation (if available)

If your report includes secrets or wallet-related material, rotate those credentials immediately after testing.

## Security baseline

- Secrets must be provided via environment variables.
- `.env` files are local-only and must not be committed.
- Image fetching enforces SSRF protections.
- Deploy flow includes idempotency and symbol dedup guardrails.
