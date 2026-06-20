# M7 — Alpha release: README, GIF, release channels, signed artifacts

**Goal:** NemotronFlow is publicly releasable as an open-source alpha. The README sells it, the docs explain it, the ROADMAP sets expectations, release channels (`stable`/`beta`/`nightly`) are wired into the updater, and the first signed artifacts are published on GitHub.

**Files to create/modify:**
- `README.md` — finalize with animated GIF, features, install, build, architecture, status.
- `docs/architecture.md`, `docs/getting-started.md`, `docs/engines.md` (how to write a `BaseASR` plugin), `docs/troubleshooting.md`, `docs/packaging.md`.
- `.github/workflows/release.yml` — add `nightly` and `beta` channel artifacts + `latest.json` per channel.
- Settings: `release_channel` field added (default `stable`); updater endpoint reads from it.
- `CHANGELOG.md` — `## [0.1.0] - 2026-MM-DD` entry.

**Dependencies:** M6.

**Acceptance criteria:**
- README leads with the elevator pitch, an animated GIF of the overlay in action, feature list, install instructions (per platform), build-from-source, architecture diagram link, and an honest "Status: alpha" line.
- `docs/engines.md` walks a contributor through implementing `BaseASR` end-to-end (the `test_engine_contract.py` requirement is called out).
- Release channels: tagging `v0.1.0` publishes `stable`; a nightly cron publishes `nightly`; pre-release tags publish `beta`. The updater manifest is per-channel.
- A first-time user (clean machine) can install, run, and use push-to-talk within 2 minutes — documented in `getting-started.md`.
- CHANGELOG entry written; LICENSE/CONTRIBUTING/SECURITY all in place; the repo reads as a serious open-source project.

**Test strategy:** No new automated tests. Manual: follow `getting-started.md` from scratch on a clean VM; verify the README GIF accurately reflects current behavior.

**Estimated complexity:** Medium (mostly writing + release plumbing).

**Commit boundaries:** one per task, tag `v0.1.0` (the alpha).

---

## Task M7.1: Capture the demo GIF

- [ ] **Step 1:** On a dev machine with M5 working, record a 10–15 s screen capture: focus a text field, press Ctrl+Space, say a clear sentence, release, watch the overlay cycle Listening → Processing → ✓ Pasted. Use OBS or Windows `Win+G` / `ffmpeg` to capture, then `ffmpeg` to crop + optimize as `docs/demo.gif` (< 3 MB).

- [ ] **Step 2:** Commit:
```bash
git add docs/demo.gif
git commit -m "docs: add animated demo gif"
```

---

## Task M7.2: Finalize README

**Files:** `README.md`.

- [ ] **Step 1:** Rewrite `README.md` with the full structure from spec §17/ROADMAP:

```markdown
# NemotronFlow

> Push-to-talk speech-to-text powered by NVIDIA Nemotron 3.5 ASR.

![demo](docs/demo.gif)

Hold **Ctrl + Space**, speak, release. Your words are transcribed and pasted
into whatever you were typing in — ChatGPT, Cursor, Claude, VSCode, Obsidian,
your browser, anywhere.

![status](https://img.shields.io/badge/status-alpha-orange)
![license](https://img.shields.io/badge/license-MIT-blue)
![platform](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-blue)

## Features
- Global push-to-talk hotkey (Ctrl + Space, configurable).
- NVIDIA Nemotron 3.5 ASR, local-first, GPU-accelerated.
- Automatic clipboard paste into any focused app, with clipboard restore.
- Local-only history (SQLite) — search, favorite, export. No telemetry.
- Premium overlay: transparent, click-through, never steals focus.
- Dark mode first. Low latency: < 400 ms release-to-paste on GPU.

## Status
Alpha. Windows is the primary target. The Nemotron production engine requires
a model download on first use; the dev stub engine runs the full loop without it.

## Install
### Windows (primary)
Download the latest `NemotronFlow_*_x64-setup.exe` from
[Releases](https://github.com/<org>/NemotronFlow/releases).

### macOS / Linux
See [Build from source](#build-from-source). Signed installers in beta channel.

## Build from source
1. Install Python 3.11+, Node 20+, pnpm 10, and the Rust toolchain.
2. `git clone … && cd NemotronFlow`
3. `make install`
4. (For the Nemotron engine) `pip install -e ".[nemo]"`
5. `make dev` runs the frontend; in another shell `python -m nemotronflow` runs the backend.

See [`docs/getting-started.md`](docs/getting-started.md) for details.

## Architecture
Tauri (Rust) shell + a long-lived Python sidecar, talking over WebSocket JSON-RPC.
See [`docs/architecture.md`](docs/architecture.md) and the
[design spec](docs/superpowers/specs/2026-06-19-nemotronflow-design.md).

## Writing an ASR engine
NemotronFlow is pluggable. See [`docs/engines.md`](docs/engines.md) — implement
`BaseASR` and pass `tests/test_engine_contract.py`.

## Roadmap
See [`ROADMAP.md`](ROADMAP.md).

## License
MIT.
```

- [ ] **Step 2:** Commit:
```bash
git add README.md
git commit -m "docs: finalize README with demo, features, install, build"
```

---

## Task M7.3: Docs (architecture, getting-started, engines, troubleshooting)

**Files:** `docs/architecture.md`, `docs/getting-started.md`, `docs/engines.md`, `docs/troubleshooting.md`.

- [ ] **Step 1:** `docs/architecture.md` — the process-model diagram, responsibility split, IPC protocol summary, latency budget table (lifted from the spec). Audience: a contributor who wants to understand the codebase.

- [ ] **Step 2:** `docs/getting-started.md` — the 2-minute path: install prerequisites, clone, `make install`, run dev, configure hotkey/mic, first utterance. Include the macOS permission-grant steps (Accessibility + Input Monitoring) and the Linux X11/Wayland notes.

- [ ] **Step 3:** `docs/engines.md` — the `BaseASR` contract, a minimal engine skeleton, the `test_engine_contract.py` requirement, and how to register in `ENGINE_REGISTRY`. Note that v1 ships NemotronASR + StubASR only; future engines are listed but unimplemented.

- [ ] **Step 4:** `docs/troubleshooting.md` — overlay not appearing (focus/permission issues), paste landing in the wrong app (overlay stealing focus on a non-Windows OS), high latency (CPU MODE badge → install CUDA), Nemotron checkpoint not found, sidecar crash loop. Each with the diagnostic step (Export diagnostics).

- [ ] **Step 5:** Commit:
```bash
git add docs
git commit -m "docs: add architecture, getting-started, engines, troubleshooting guides"
```

---

## Task M7.4: Release channels (settings + CI)

**Files:** `frontend/src-tauri/tauri.conf.json` (updater endpoints per channel), `.github/workflows/release.yml` (add nightly + beta jobs), `backend/nemotronflow/settings.py` (add `release_channel` field).

- [ ] **Step 1:** Add `release_channel: str = "stable"` to `Settings`. The Tauri updater endpoint is built from it: `<release-base>/<channel>/latest.json`.

- [ ] **Step 2:** Extend `.github/workflows/release.yml`:
  - On `v*.*.*` tag → `stable` channel artifacts + `stable/latest.json`.
  - On `v*.*.*-beta*` tag → `beta` channel.
  - Nightly cron (02:00 UTC) → `nightly` channel (builds from `main`, no tag).

- [ ] **Step 3 (verify):** trigger a dry-run workflow on a `-beta` tag; confirm artifacts upload and `beta/latest.json` is written.

- [ ] **Step 4:** Commit:
```bash
git add -A
git commit -m "feat(release): add stable/beta/nightly channels and release_channel setting"
```

---

## Task M7.5: CHANGELOG + clean-machine acceptance + tag alpha

- [ ] **Step 1:** Write `CHANGELOG.md` `## [0.1.0] - 2026-MM-DD` under `## [Unreleased]`, listing the M0–M7 features.

- [ ] **Step 2 (clean-machine acceptance):** On a fresh Windows VM with no Python/Node installed, download the installer, install, launch, configure mic + hotkey, and complete a real push-to-talk utterance into Notepad. Document any friction in `docs/troubleshooting.md`. This is the gate for "alpha."

- [ ] **Step 3 (final lint + test):**
```bash
make lint && make test
```

- [ ] **Step 4:** Commit + tag the alpha:
```bash
git add -A
git commit -m "chore(release): 0.1.0 alpha (clean-machine acceptance passed)"
git tag v0.1.0
```

**M7 commit boundary:** `v0.1.0` — NemotronFlow alpha is released. The repo reads as a serious open-source project, installers are downloadable, and a first-time user can use the product in two minutes.

---

## Done

At `v0.1.0` the MVP is complete: a premium, Wispr-Flow-equivalent push-to-talk desktop app powered by NVIDIA Nemotron 3.5 ASR, with local history, configurable settings, an installable bundle, and a clean open-source posture. Everything in the design spec's "Future" sections (streaming partials, VAD, LLM cleanup, voice commands, semantic history, additional engines) is a v1.1/v2 follow-on with the architecture already prepared for it.
