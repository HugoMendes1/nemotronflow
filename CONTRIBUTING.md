# Contributing to NemotronFlow

Thanks for your interest in improving NemotronFlow! This guide covers the
essentials for getting a change landed.

## Development setup

```bash
git clone <repo-url> NemotronFlow
cd NemotronFlow
make install        # installs python deps (editable) + frontend deps
make dev            # runs the frontend dev server
# in another shell:
python -m nemotronflow   # runs the python sidecar (M1a onward)
```

Prerequisites: Python 3.11+, Node 20+, pnpm 10, git. The Rust toolchain is
required for Tauri work (M2 onward).

## Daily workflow

1. Create a branch off `main`: `git switch -c feat/<short-description>`.
2. Make your changes. Follow the existing style — the repo enforces it via
   `make lint` (ruff + black + mypy on Python; eslint + tsc on TypeScript;
   `cargo fmt` + `cargo clippy` on Rust once present).
3. Write tests. Every behavior change ships with a test. The project uses TDD
   throughout (see the plan docs).
4. Run `make lint && make test` locally before pushing. CI runs the same gates.
5. Commit using [Conventional Commits](https://www.conventionalcommits.org/):
   `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `build:`,
   `ci:`, `perf:`. Keep commits focused and atomic.
6. Open a pull request against `main`. Fill in the PR template.

## Pull request checklist

- [ ] `make lint` passes.
- [ ] `make test` passes.
- [ ] Tests added for new behavior.
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` for user-facing changes.
- [ ] Docs updated if public behavior changed.

## Adding an ASR engine

NemotronFlow is pluggable via the `BaseASR` interface. A new engine MUST pass
`tests/test_engine_contract.py` — that test enforces the contract every engine
shares. See `docs/engines.md` (added in M7) for the full walkthrough. Until
then, the contract is documented in
[`backend/nemotronflow/asr/base.py`](../backend/nemotronflow/asr/base.py)
(added in M1b) and the design spec's §5.

## Branching and releases

- `main` is always green and shippable. Branch protection requires green CI
  plus one review.
- Tags `v*.*.*` trigger release builds; see `.github/workflows/` (added in M0).
- Release channels: `stable`, `beta`, `nightly` (added in M7).

## Code style

- Python: full type hints, `from __future__ import annotations`, docstrings on
  public APIs, line length 100 (black + ruff enforce this).
- TypeScript: `strict: true`, no `any` without an inline eslint disable and a
  comment explaining why.
- Rust: `cargo fmt` formatting, `cargo clippy` clean (`-D warnings`).
- No `print()` in Python — use the structured logger
  (`from nemotronflow.logging import get_logger`).

## Reporting issues

Bugs and features go through GitHub Issues using the templates in
`.github/ISSUE_TEMPLATE/`. Security issues are private — see
[`SECURITY.md`](SECURITY.md).
