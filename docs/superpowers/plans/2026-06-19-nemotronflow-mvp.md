# NemotronFlow MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an alpha-quality, Wispr-Flow-equivalent push-to-talk desktop app powered by NVIDIA Nemotron 3.5 ASR, end-to-end, in 8 incremental milestones.

**Architecture:** Tauri (Rust) shell launches and supervises a long-lived Python sidecar. Python owns hotkeys (pynput), audio capture (sounddevice, circular ring buffer), ASR (`BaseASR` + `NemotronASR`/`StubASR`), history (SQLite), settings, LLM cleanup. Tauri owns the overlay + settings + history windows, tray, clipboard (arboard), auto-paste (enigo), and the WebSocket JSON-RPC bridge. The two halves talk over a single localhost WebSocket using JSON-RPC 2.0.

**Tech Stack:**
- **Frontend:** React 18, TypeScript, Vite, Tauri v2, Rust (arboard, enigo, tokio-tungstenite, tauri-plugin-shell)
- **Backend:** Python 3.11+, sounddevice, numpy, pynput, pyperclip *(unused in v1 — clipboard is Rust-side)*, websockets, structlog, aiosqlite/aiosqlite, NeMo (M5 only)
- **Tests:** pytest, pytest-asyncio, pytest-benchmark, vitest, cargo test, Playwright/tauri-driver (nightly)
- **Tooling:** ruff, black, mypy --strict, cargo fmt/clippy, eslint, tsc, PyInstaller (--onedir)

**Spec reference:** `docs/superpowers/specs/2026-06-19-nemotronflow-design.md`

**Prerequisite (must be done before M1a):** install the Rust toolchain + Tauri prerequisites — `winget install Rustlang.Rustup` then `cargo install tauri-cli --version "^2.0"`, plus MSVC build tools and WebView2 (preinstalled on Win11). Confirm `rustc --version` works.

---

## File Structure

This is the complete file map for the MVP. Tasks reference these paths exactly. Order reflects build order, not final layout.

### Repository root

```
NemotronFlow/
├── .gitignore, .gitattributes
├── .github/workflows/ci.yml
├── .github/ISSUE_TEMPLATE/{bug_report,feature_request,engine_plugin}.md
├── .github/PULL_REQUEST_TEMPLATE.md, CODEOWNERS
├── LICENSE                              # MIT
├── README.md
├── ROADMAP.md
├── CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, CHANGELOG.md
├── Makefile
├── package.json                         # pnpm workspace root
├── pnpm-workspace.yaml
├── pyproject.toml
├── requirements.txt, requirements-dev.txt
├── .env.example
└── docs/
    ├── architecture.md, getting-started.md, engines.md, troubleshooting.md
    └── superpowers/{specs,plans}/...
```

### Backend (Python sidecar)

```
backend/
├── pyproject.toml                       # backend-local packaging (optional; can live in root)
├── nemotronflow.spec                    # PyInstaller spec (M6)
└── nemotronflow/
    ├── __init__.py                      # version, public API
    ├── __main__.py                      # `python -m nemotronflow` → server.run()
    ├── server.py                        # WebSocket JSON-RPC server, lifecycle, READY line
    ├── hotkeys.py                       # pynput global hotkey, press/release hooks
    ├── audio_capture.py                 # sounddevice + circular ring buffer, real-time safe
    ├── transcription.py                 # orchestrator: capture-end → engine → result
    ├── history.py                       # SQLite add/delete/edit/clear/export, async worker
    ├── settings.py                      # Settings dataclass, load/save/validate, change events
    ├── llm_cleanup.py                   # identity transform in v1
    ├── logging.py                       # structlog setup, session_id/utterance_id
    ├── diagnostics.py                   # zip builder with redaction (M6)
    ├── asr/
    │   ├── __init__.py
    │   ├── base.py                      # BaseASR, AudioChunk, TranscriptionResult, PartialResult
    │   ├── nemotron.py                  # NemotronASR (M5)
    │   ├── stub.py                      # StubASR (M1b)
    │   └── factory.py                   # ENGINE_REGISTRY, EngineManager singleton
    ├── ipc/
    │   ├── __init__.py
    │   ├── protocol.py                  # JSON-RPC 2.0 envelope, method catalogue, types
    │   └── transport.py                 # WebSocket framing, reconnect
    └── config/
        └── default_settings.json

tests/
├── conftest.py                          # fixtures: tmp data dir, fake audio, stub engine
├── test_ring_buffer.py
├── test_hotkeys.py                      # logic only; pynput mocked
├── test_settings.py
├── test_history.py
├── test_ipc_protocol.py
├── test_ipc_transport.py
├── test_server.py                       # integration: WS server + fake client
├── test_transcription.py
├── test_engine_contract.py              # parametrized over NemotronASR + StubASR
├── test_stub_asr.py
├── test_nemotron_asr.py                 # @pytest.mark.gpu on real path
├── test_engine_manager.py
├── test_llm_cleanup.py
├── test_diagnostics.py
└── benchmarks/
    ├── test_hotkey_latency.py
    ├── test_asr_latency.py
    ├── test_paste_latency.py            # stub-only here; real paste in E2E
    └── test_startup_time.py
```

### Frontend (Tauri + React)

```
frontend/
├── package.json, tsconfig.json, vite.config.ts, index.html, eslint.config.js
├── src/
│   ├── main.tsx                         # mounts <App/>
│   ├── App.tsx                          # HashRouter: #/overlay | #/settings | #/history
│   ├── overlay/
│   │   ├── Overlay.tsx
│   │   ├── OverlayStates.tsx            # 5 states
│   │   ├── LevelMeter.tsx
│   │   └── overlay.css
│   ├── settings/
│   │   ├── SettingsPage.tsx
│   │   ├── sections/{General,Audio,Engine,Output,History,Advanced,Dev}Tab.tsx
│   │   └── controls/{HotkeyPicker,MicDropdown,Toggle,Select,Slider}.tsx
│   ├── history/
│   │   ├── HistoryPage.tsx              # virtualized
│   │   ├── HistoryItem.tsx, HistoryActions.tsx, HistoryEmptyState.tsx
│   │   └── useVirtualList.ts
│   ├── rpc/
│   │   ├── client.ts                    # WebSocket JSON-RPC client (typed)
│   │   ├── events.ts                    # EventTarget wrapper
│   │   └── types.ts                     # mirror of protocol.py
│   ├── hooks/
│   │   ├── useBackend.ts
│   │   ├── useBackendEvent.ts
│   │   ├── useSettings.ts
│   │   ├── useHistory.ts
│   │   └── useEngineStatus.ts
│   ├── theme/
│   │   ├── tokens.css                   # dark-first
│   │   └── tokens.ts
│   └── assets/
│       ├── logo.svg
│       └── tray-icons/{idle,listening,processing,done}.svg
└── src-tauri/
    ├── Cargo.toml, tauri.conf.json, build.rs, icons/
    └── src/
        ├── main.rs                      # app entry, tray, window spawning
        ├── sidecar.rs                   # spawn Python, read READY line, port handoff
        ├── rpc.rs                       # WS client + JSON-RPC dispatcher
        ├── clipboard.rs                 # arboard wrapper
        ├── autopaste.rs                 # enigo Ctrl+V + restore scheduling
        ├── tray.rs
        ├── overlay.rs                   # click-through + WS_EX_NOACTIVATE
        ├── diagnostics.rs               # diagnostics export wiring
        └── logging.rs

frontend/src/__tests__/                  # vitest
├── rpc/client.test.ts
├── hooks/{useSettings,useHistory,useBackend,useEngineStatus}.test.ts
├── overlay/Overlay.test.tsx
├── settings/{SettingsPage,validation}.test.tsx
└── history/useVirtualList.test.ts
```

### Shared contracts (the "API" between halves)

These are duplicated by intent (one Python, one TypeScript) and must stay in lockstep:
- `backend/nemotronflow/ipc/protocol.py` ↔ `frontend/src/rpc/types.ts`
- `backend/nemotronflow/settings.py::Settings` ↔ settings forms in React
- `backend/nemotronflow/asr/base.py::TranscriptionResult` ↔ `transcription/result` payload shape

A `tests/test_protocol_parity.py` test (M1a) asserts that the canonical list of method names in `protocol.py` equals the list in `types.ts` (parsed via regex). This catches drift.

---

## Milestone Index

| Milestone | Shippable outcome | Depends on | Complexity | Plan file |
|---|---|---|---|---|
| **M0** | Repo scaffold + tooling + CI green | — | Low | [`m0-scaffold.md`](./m0-scaffold.md) |
| **M1a** | Python sidecar boots, WS server, JSON-RPC round-trip, READY line | M0 | Medium | [`m1a-sidecar-ipc.md`](./m1a-sidecar-ipc.md) |
| **M1b** | Circular ring buffer + pynput hotkey + StubASR end-to-end transcription | M1a | Medium-High | [`m1b-audio-hotkey-stub.md`](./m1b-audio-hotkey-stub.md) |
| **M2** | Tauri 3-window shell + overlay (5 states) + tray + WS bridge | M1b | High | [`m2-tauri-overlay.md`](./m2-tauri-overlay.md) |
| **M3** | Clipboard backup + auto-paste + restore modes (Rust) | M2 | Medium | [`m3-clipboard-autopaste.md`](./m3-clipboard-autopaste.md) |
| **M4** | SQLite history + virtualized History UI + Settings UI | M3 | High | [`m4-history-settings.md`](./m4-history-settings.md) |
| **M5** | NemotronASR (NeMo) + EngineManager lifecycle + warmup + model download | M4 | High | [`m5-nemotron-asr.md`](./m5-nemotron-asr.md) |
| **M6** | PyInstaller --onedir + Tauri bundling + installers + diagnostics export | M5 | High | [`m6-packaging.md`](./m6-packaging.md) |
| **M7** | Alpha: README/GIF, ROADMAP, release channels, signed artifacts | M6 | Medium | [`m7-alpha-release.md`](./m7-alpha-release.md) |

**Each milestone is a standalone document in this directory** (linked above) with full TDD task breakdowns: Goals, Files, Dependencies, Acceptance criteria, Test strategy, Estimated complexity, Commit boundaries. Read this index for the map; open a milestone file for the executable tasks.

Each milestone ends with a tagged commit (`v0.1.0-m1a`, …, `v0.1.0-m7` alpha) and a green CI run.

**Global conventions for every task:**
- TDD: write failing test → run to confirm fail → implement → run to confirm pass → commit.
- All Python files: full type hints, `from __future__ import annotations` at top, docstrings on public APIs.
- All TS files: strict mode, no `any` without `// eslint-disable-next-line` + a comment.
- Every commit message: conventional commits (`feat:`, `test:`, `chore:`, `refactor:`, `docs:`).
- No `print()`; use the logger (M0 ships it).
- Run `make lint` before every commit; CI runs the same gates.

**A note on scope.** This plan is large. It is written so each milestone produces independently runnable software, and tasks within each milestone are independently committable. M1a–M3 give a complete dev loop (hold key → StubASR transcript → auto-paste) on the user's machine. M4–M7 add real engine, packaging, and release polish. If executing, prefer **subagent-driven-development** so each task gets a fresh-context implementation pass with review between tasks.

---
