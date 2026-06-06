## What this changes

<!-- One or two sentences. The diff covers the rest. -->

## Why

<!-- The motivation: a bug it fixes, a use case it enables, a constraint
that forced a refactor. Link the issue / discussion if there is one. -->

Closes #

## Test plan

<!-- The commands you ran and what you verified. Reviewers should be
able to repeat this. Skip the obvious (don't say "ran the tests") and
call out what's *not* covered by automated tests, UI, hardware,
manual flows. -->

- [ ] `pytest -q`
- [ ] `ruff check . && ruff format --check .`
- [ ] `mypy app`
- [ ] Manually verified: …

## Checklist

- [ ] Tests added for new behaviour (or note why none made sense)
- [ ] `ruff` + `mypy` clean
- [ ] User-facing changes documented (README, `docs/`, or both)
- [ ] CHANGELOG entry added under `## [Unreleased]` if user-facing
- [ ] `pyproject.toml` version bumped if this is going to be tagged
