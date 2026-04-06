# Contributing Guide

Thanks for contributing to Clank&Claw.

## Development setup

1. Create and activate a Python virtual environment.
2. Install Python dependencies with `pip install -r requirements.txt`.
3. Install Node bridge dependencies with `npm install`.
4. Copy `.env.example` to `.env` and set required values.

## Branch and commit standards

- Use focused commits with clear messages.
- Keep one concern per pull request where possible.
- Reference issue IDs in commit and PR descriptions when relevant.

## Pull request checklist

- [ ] Tests pass locally (`pytest -q`)
- [ ] Docs updated when behavior/config/ops changes
- [ ] No secrets committed
- [ ] Changelog updated for user-visible behavior changes

## Code quality expectations

- Prefer deterministic behavior and explicit failure modes.
- Maintain idempotency and deploy safety guardrails.
- Keep operational logs actionable for on-call use.

## Reporting issues

Use GitHub Issues with reproducible steps, expected behavior, and actual behavior.
