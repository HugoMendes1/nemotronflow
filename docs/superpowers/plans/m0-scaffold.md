# M0 — Repository scaffold and tooling

**Goal:** A green CI pipeline on an empty-but-real repo. Everything later builds on this.

**Files to create:** root config + docs shell + `logging.py` + Makefile + CI workflow.

**Dependencies:** Rust toolchain installed (prerequisite); Node 20+, Python 3.11+, pnpm, git.

**Acceptance criteria:**
- `git init` done; first commit on `main`.
- `make lint` runs ruff + black + mypy + tsc + eslint and passes on a trivial sample.
- `make test` runs pytest + vitest + cargo test and passes (even if empty).
- `.github/workflows/ci.yml` runs `make lint` + `make test` on ubuntu/macos/windows, green.
- MIT LICENSE, README stub, ROADMAP stub present.
- `logging.py` provides a configured structlog logger with `session_id`.

**Test strategy:** lint + test pipelines pass on the scaffold. No feature tests beyond the logging unit test.

**Estimated complexity:** Low (one focused session).

**Commit boundaries:** `v0.1.0-m0-scaffold` → `v0.1.0-m0`.

---

## Task M0.1: Initialize repo and base files

**Files:** Create `.gitignore`, `.gitattributes`, `LICENSE`, `README.md`, `ROADMAP.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`, `.env.example`.

- [ ] **Step 1:** `git init` in `NemotronFlow/`. Create `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
build/
dist/

# Node / frontend
node_modules/
frontend/dist/
frontend/src-tauri/target/

# Models (never commit)
backend/nemotronflow/models/*
!backend/nemotronflow/models/.gitkeep

# App data (local runs)
.nemotronflow/

# OS
.DS_Store
Thumbs.db

# IDE
.idea/
.vscode/
*.swp

# Env
.env
```

- [ ] **Step 2:** Create `LICENSE` — full MIT text, copyright `NemotronFlow Contributors`, year `2026`.

- [ ] **Step 3:** Create `README.md`:

````markdown
# NemotronFlow

> Push-to-talk speech-to-text powered by NVIDIA Nemotron 3.5 ASR.

Hold **Ctrl + Space**, speak, release. Your words are transcribed and pasted
into whatever you were typing in.

![status](https://img.shields.io/badge/status-alpha-orange)
![license](https://img.shields.io/badge/license-MIT-blue)

## Status
Alpha. The Nemotron production engine requires a model download. The dev stub
engine lets the full app loop run without one.

## Install / Build from source
*(Filled in at M7.)*

## Architecture
See [`docs/architecture.md`](docs/architecture.md) and the
[design spec](docs/superpowers/specs/2026-06-19-nemotronflow-design.md).

## Roadmap
See [`ROADMAP.md`](ROADMAP.md).

## License
MIT.
````

- [ ] **Step 4:** Create `ROADMAP.md`:

````markdown
# Roadmap

## V1 (alpha — this plan)
Push-to-talk, NemotronASR + StubASR, clipboard auto-paste, history, settings, packaging.

## V1.1
Streaming partial transcripts. Level-meter waveform. Resumable download hardening.

## V2
VAD + silence auto-stop. LLM cleanup (OpenAI/Claude/Gemini/Ollama/LM Studio).
Voice command mode. Semantic history search.

## Future engines
WhisperASR, ParakeetASR, DeepgramASR, OpenAIRealtimeASR, GeminiLiveASR.
````

- [ ] **Step 5:** Create stub docs: `CONTRIBUTING.md` (clone-and-PR basics), `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1 verbatim), `SECURITY.md` (report via GitHub Security Advisories, no public issues for vulns), `CHANGELOG.md` (keepachangelog header + `## [Unreleased]`), `.env.example` (comment: no env vars needed in v1).

- [ ] **Step 6:** Commit:
```bash
git add -A
git commit -m "chore: initialize repository scaffold and docs"
git tag v0.1.0-m0-scaffold
```

---

## Task M0.2: Python project + tooling config + logging (TDD)

**Files:** Create `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `backend/nemotronflow/__init__.py`, `backend/nemotronflow/logging.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_logging.py`.

- [ ] **Step 1:** Write `pyproject.toml`:

```toml
[project]
name = "nemotronflow"
version = "0.1.0"
description = "Push-to-talk speech-to-text powered by NVIDIA Nemotron 3.5 ASR."
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
authors = [{ name = "NemotronFlow Contributors" }]
dependencies = [
    "numpy>=1.26",
    "sounddevice>=0.4.6",
    "pynput>=1.7.6",
    "websockets>=12.0",
    "structlog>=24.1",
    "aiosqlite>=0.19.0",
    "pydantic>=2.6",
]

[project.optional-dependencies]
nemo = ["nemo-toolkit[asr]>=1.23"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-benchmark>=4.0",
    "pytest-cov>=4.1",
    "ruff>=0.3",
    "black>=24.2",
    "mypy>=1.8",
]

[project.scripts]
nemotronflow = "nemotronflow.__main__:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["backend"]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["backend", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "PL", "RUF"]
ignore = ["PLR0913"]

[tool.black]
line-length = 100
target-version = ["py311"]

[tool.mypy]
python_version = "3.11"
strict = true
packages = ["backend.nemotronflow"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "gpu: tests requiring a CUDA GPU and real NeMo (skipped by default)",
]

[[tool.mypy.overrides]]
module = ["sounddevice.*", "pynput.*", "nemo.*"]
ignore_missing_imports = true
```

- [ ] **Step 2:** Write `requirements.txt` (top-level pinned ranges) and `requirements-dev.txt` (`-r requirements.txt` + dev tools from `pyproject.toml`'s `dev` extra).

- [ ] **Step 3:** Write `backend/nemotronflow/__init__.py`:

```python
"""NemotronFlow backend: push-to-talk speech-to-text sidecar."""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
```

- [ ] **Step 4 (write failing test):** `tests/test_logging.py`:

```python
from __future__ import annotations

import json

from nemotronflow.logging import get_logger, reset_session


def test_logger_emits_jsonl_with_session_and_utterance_ids() -> None:
    reset_session(session_id="sess-abc")
    log = get_logger("test")
    log.info("transcribe_done", utterance_id="utt-1", inference_ms=287)
    record = get_logger.last_record()
    assert record is not None
    assert record["event"] == "transcribe_done"
    assert record["session_id"] == "sess-abc"
    assert record["utterance_id"] == "utt-1"
    assert record["inference_ms"] == 287
    # JSONL contract: the record must be JSON-serializable.
    json.loads(json.dumps(record))
```

- [ ] **Step 5 (run, confirm fail):**
```bash
pytest tests/test_logging.py -v
```
Expected: `ModuleNotFoundError: nemotronflow.logging`.

- [ ] **Step 6 (implement):** `backend/nemotronflow/logging.py`:

```python
"""Structured logging for NemotronFlow.

Every record carries `session_id` (one per process boot) and, when relevant,
`utterance_id`. Output is JSONL to a rotating file plus a human-readable
mirror to stderr in dev. A module-level test sink captures the last record.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import uuid
from pathlib import Path
from typing import Any

import structlog

_CURRENT_SESSION_ID: str = ""
_LAST_RECORD: dict[str, Any] | None = None


def reset_session(session_id: str | None = None) -> None:
    """(Test helper.) Start a fresh session id and clear the test sink."""
    global _CURRENT_SESSION_ID, _LAST_RECORD
    _CURRENT_SESSION_ID = session_id or uuid.uuid4().hex
    _LAST_RECORD = None


def session_id() -> str:
    global _CURRENT_SESSION_ID
    if not _CURRENT_SESSION_ID:
        _CURRENT_SESSION_ID = uuid.uuid4().hex
    return _CURRENT_SESSION_ID


def _test_sink(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    global _LAST_RECORD
    _LAST_RECORD = dict(event_dict)
    return event_dict


def _session_injector(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("session_id", session_id())
    return event_dict


def configure(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Configure structlog. Idempotent. Safe in tests with log_dir=None."""
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _session_injector,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _test_sink,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "nemotronflow.log", maxBytes=5 * 1024 * 1024, backupCount=5
        )
        root = logging.getLogger("nemotronflow")
        root.addHandler(handler)
        root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str = "nemotronflow") -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Also exposes get_logger.last_record() for tests."""
    log = structlog.get_logger(name)

    def _last_record() -> dict[str, Any] | None:
        return _LAST_RECORD

    setattr(get_logger, "last_record", _last_record)
    return log  # type: ignore[return-value]
```

- [ ] **Step 7 (run, confirm pass):**
```bash
pytest tests/test_logging.py -v
```
Expected: PASS.

- [ ] **Step 8:** Write `tests/__init__.py` (empty) and `tests/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from nemotronflow import logging as nf_logging


@pytest.fixture(autouse=True)
def _isolated_logging() -> None:
    nf_logging.reset_session(session_id="sess-test")
    nf_logging.configure(level="DEBUG", log_dir=None)


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("NEMOTRONFLOW_DATA_DIR", str(data))
    return data
```

- [ ] **Step 9:** Commit:
```bash
git add -A
git commit -m "feat: add python project config and structured logging with session/utterance ids"
```

---

## Task M0.3: Frontend scaffold + Node tooling (TDD)

**Files:** Create `package.json`, `pnpm-workspace.yaml`, `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/eslint.config.js`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/theme/tokens.css`, `frontend/src/__tests__/setup.ts`, `frontend/src/__tests__/App.test.tsx`.

- [ ] **Step 1:** Root `package.json`:
```json
{
  "name": "nemotronflow",
  "private": true,
  "version": "0.1.0",
  "license": "MIT",
  "packageManager": "pnpm@10.0.0",
  "scripts": {
    "lint": "pnpm -C frontend lint",
    "test": "pnpm -C frontend test",
    "dev": "pnpm -C frontend dev",
    "build": "pnpm -C frontend build"
  }
}
```

- [ ] **Step 2:** `pnpm-workspace.yaml`:
```yaml
packages:
  - frontend
```

- [ ] **Step 3:** `frontend/package.json` with React 18, react-router-dom 6, vite 5, vitest 1, @testing-library/react 14, eslint 8, typescript 5 (versions as listed in the plan header's File Structure). Scripts: `dev`, `build` (`tsc --noEmit && vite build`), `lint`, `test` (`vitest run`), `test:watch`.

- [ ] **Step 4:** `frontend/tsconfig.json` — `strict: true`, `noUnusedLocals/Parameters: true`, `jsx: react-jsx`, `target: ES2022`, `noEmit: true`.

- [ ] **Step 5:** `frontend/vite.config.ts`:
```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: { target: "es2022", outDir: "dist" },
  test: { environment: "jsdom", setupFiles: ["./src/__tests__/setup.ts"] },
});
```

- [ ] **Step 6:** `frontend/eslint.config.js` — flat config, recommended + `@typescript-eslint`, `"@typescript-eslint/no-explicit-any": "error"`.

- [ ] **Step 7:** `frontend/index.html` (root div + module script), `frontend/src/main.tsx` (renders `<App/>` with `tokens.css` imported), `frontend/src/App.tsx` placeholder returning `<div>NemotronFlow</div>`.

- [ ] **Step 8:** `frontend/src/theme/tokens.css` — the full dark-first token set from spec §7.6 (CSS custom properties for `--bg-*`, `--accent`, `--success/warning/danger`, `--radius-*`, `--ease-out`, `--dur-*`, plus `[data-theme="light"]` overrides and the `body` font-family: `Inter, system-ui, -apple-system, sans-serif`).

- [ ] **Step 9 (write failing test):** `frontend/src/__tests__/setup.ts`:
```ts
import "@testing-library/jest-dom";
```
`frontend/src/__tests__/App.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { App } from "../App";

describe("App", () => {
  it("renders the app name", () => {
    render(<App />);
    expect(screen.getByText("NemotronFlow")).toBeInTheDocument();
  });
});
```

- [ ] **Step 10 (install + run + commit):**
```bash
pnpm install
pnpm -C frontend test
pnpm -C frontend lint
git add -A
git commit -m "feat: scaffold react+typescript+vite frontend with tooling"
```

---

## Task M0.4: Makefile + CI workflow + GitHub templates

**Files:** Create `Makefile`, `.github/workflows/ci.yml`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS`, `.github/ISSUE_TEMPLATE/{bug_report,feature_request,engine_plugin}.md`.

- [ ] **Step 1:** `Makefile` with targets: `install`, `lint` (ruff check + format check + mypy + frontend lint + tsc), `test` (`test-python` + `test-frontend`), `test-python` (`pytest -m "not gpu"`), `test-frontend` (`pnpm -C frontend test`), `build`, `dev`. All `.PHONY`.

- [ ] **Step 2:** `.github/workflows/ci.yml` — matrix `ubuntu-latest/macos-latest/windows-latest`, setup-python 3.11, pnpm 10, node 20, install deps, run lint + test. (`Make lint` / `make test` invoked directly so the same gates run locally and in CI.)

- [ ] **Step 3:** `PULL_REQUEST_TEMPLATE.md` (summary + checklist: lint/test pass, tests added, CHANGELOG updated). `CODEOWNERS` (`* @nemotronflow/maintainers`, `/backend/ @nemotronflow/backend`, `/frontend/ @nemotronflow/frontend`). Three issue templates; `engine_plugin.md` includes a BaseASR contract reminder pointing to `tests/test_engine_contract.py`.

- [ ] **Step 4 (verify + tag):**
```bash
make install
make lint
make test
git add -A
git commit -m "chore: add Makefile, CI workflow, PR/issue templates"
git tag v0.1.0-m0
```

**M0 commit boundary:** `v0.1.0-m0` — green CI on all three OSes with real lint/test pipelines. At this point you have an empty app skeleton that lints and tests green.
