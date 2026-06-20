# M6 — PyInstaller --onedir + Tauri bundling + installers + diagnostics export

**Goal:** NemotronFlow ships as a single, double-clickable installer per platform. The Python sidecar is frozen with PyInstaller (`--onedir`, never `--onefile`), bundled as a Tauri sidecar, and wrapped in a platform installer (NSIS on Windows, DMG on macOS, deb/AppImage on Linux). A user-initiated "Export diagnostics" feature produces a redacted zip.

**Files to create/modify:**
- `backend/nemotronflow.spec` — PyInstaller spec.
- `frontend/src-tauri/tauri.conf.json` — `bundle.externalBin` for the sidecar, product metadata, icons.
- `frontend/src-tauri/src/diagnostics.rs` — diagnostics export wiring.
- `backend/nemotronflow/diagnostics.py` — log/settings/health bundler with redaction.
- `tests/test_diagnostics.py`.
- `.github/workflows/release.yml` — release job building + uploading installers.

**Dependencies:** M5. PyInstaller + a Windows build for the primary target.

**Acceptance criteria:**
- `pyinstaller backend/nemotronflow.spec` produces a `dist/nemotronflow-sidecar/` that runs standalone and prints the READY line.
- `cargo tauri build` produces a signed-able installer that, when run, installs NemotronFlow with a Start Menu entry and runs from the tray on launch.
- The bundled app boots the sidecar, connects, and the full push-to-talk loop works on a clean machine without Python installed.
- Diagnostics export: user clicks tray → Export diagnostics → confirms → a redacted zip lands on the Desktop. API keys/tokens/secret paths redacted; `history.db` excluded by default (opt-in checkbox).
- Tauri updater config points at a GitHub Releases `latest.json`; updates are silent-checked on startup.
- **PyInstaller never uses `--onefile`.** `--onedir` only.

**Test strategy:** `test_diagnostics.py` for the redaction + bundling logic. PyInstaller + Tauri builds verified manually on at least Windows (primary) before tagging. CI builds all three OSes; manual smoke-test Windows installer.

**Estimated complexity:** High. NeMo's native deps + PyInstaller hidden imports are the gnarliest part; expect iteration on the spec.

**Commit boundaries:** one per task, tag `v0.1.0-m6`.

---

## Task M6.1: Diagnostics export with redaction (TDD)

**Files:** `backend/nemotronflow/diagnostics.py`, `tests/test_diagnostics.py`.

- [ ] **Step 1 (write failing test):** `tests/test_diagnostics.py`:

```python
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from nemotronflow.diagnostics import build_diagnostics_zip, redact_settings


def test_redact_settings_masks_keys_and_secret_paths() -> None:
    settings = {
        "hotkey": "ctrl+space",
        "llm_cleanup_provider": "openai",
        "openai_api_key": "sk-xxxxxx",
        "auth_token": "tok-secret",
        "nemotron_checkpoint_path": "C:/Users/bob/secrets/model.nemo",
    }
    red = redact_settings(settings)
    assert red["openai_api_key"] == "[REDACTED]"
    assert red["auth_token"] == "[REDACTED]"
    assert "secrets" not in red["nemotron_checkpoint_path"]
    assert red["hotkey"] == "ctrl+space"  # non-secret untouched


@pytest.mark.asyncio
async def test_build_zip_excludes_history_by_default(tmp_data_dir: Path, tmp_path: Path) -> None:
    # Seed logs + a fake history db + settings.
    logs = tmp_data_dir / "logs"; logs.mkdir()
    (logs / "nemotronflow.log").write_text("event=test\n", encoding="utf-8")
    (tmp_data_dir / "history.db").write_bytes(b"sqlite-ish")
    (tmp_data_dir / "settings.json").write_text(json.dumps({"hotkey": "ctrl+space"}), encoding="utf-8")

    out = await build_diagnostics_zip(dest=tmp_path / "diag.zip", include_history=False)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "logs/nemotronflow.log" in names or any("nemotronflow.log" in n for n in names)
    assert "settings.json" in names or any("settings" in n for n in names)
    assert not any("history.db" in n for n in names)  # excluded by default


@pytest.mark.asyncio
async def test_build_zip_includes_history_when_opted_in(tmp_data_dir: Path, tmp_path: Path) -> None:
    (tmp_data_dir / "history.db").write_bytes(b"sqlite-ish")
    out = await build_diagnostics_zip(dest=tmp_path / "diag.zip", include_history=True)
    with zipfile.ZipFile(out) as z:
        assert any("history.db" in n for n in z.namelist())
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_diagnostics.py -v`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/diagnostics.py` — `redact_settings` masks any key matching `*_key|*token|*secret|password|api_*` and scrubs path segments containing `secret/creds/keys`. `build_diagnostics_zip(include_history)` zips the last 5 MB of logs (truncated), redacted settings.json, an engine `health()` snapshot, and OS/GPU/mic info; conditionally includes `history.db`. Writes to the provided dest.

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_diagnostics.py -v`.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/diagnostics.py tests/test_diagnostics.py
git commit -m "feat(diagnostics): add redacted diagnostics zip builder"
```

---

## Task M6.2: PyInstaller spec (--onedir)

**Files:** `backend/nemotronflow.spec`, modify `pyproject.toml` (add `pyinstaller` to an optional `packaging` extra).

- [ ] **Step 1:** Write `backend/nemotronflow.spec` targeting `python -m PyInstaller`:

```python
# nemotronflow.spec -- always --onedir (fast startup > single file).
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

hiddenimports = (
    collect_submodules("sounddevice")
    + collect_submodules("pynput")
    + collect_submodules("structlog")
    + collect_submodules("aiosqlite")
    + ["nemotronflow.__main__", "nemotronflow.server"]
)

datas = collect_data_files("nemotronflow")

a = Analysis(
    ["../run_sidecar.py"],  # thin entry that calls nemotronflow.__main__:main
    pathex=["backend"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="nemotronflow-sidecar",
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="nemotronflow-sidecar")
```

Also create `run_sidecar.py` (one line: `from nemotronflow.__main__ import main; main()`).

**NeMo packaging note:** if bundling the GPU artifact, add NeMo's `collect_submodules("nemo")` + `collect_data_files("nemo")`. The light installer excludes NeMo entirely (stub-only) and downloads it on demand isn't feasible for native libs — so the **light installer ships CPU-only without NeMo**, and the **GPU bundle** is a separate build with NeMo collected. Document this in `docs/packaging.md`.

- [ ] **Step 2 (build + smoke):**
```bash
cd backend
pyinstaller nemotronflow.spec --noconfirm --distpath ../dist
../dist/nemotronflow-sidecar/nemotronflow-sidecar.exe   # confirm READY line prints
```

- [ ] **Step 3:** Commit:
```bash
git add backend/nemotronflow.spec run_sidecar.py pyproject.toml docs/packaging.md
git commit -m "build(pyinstaller): add --onedir spec with sounddevice/pynput hidden imports"
```

---

## Task M6.3: Tauri bundling + external sidecar

**Files:** `frontend/src-tauri/tauri.conf.json`, `frontend/src-tauri/src/sidecar.rs` (use Tauri's sidecar API), build script.

- [ ] **Step 1:** In `tauri.conf.json`, set `bundle.externalBin` to `["../dist/nemotronflow-sidecar/nemotronflow-sidecar"]` (Tauri appends the platform triple). Configure `bundle.identifier`, `productName`, icons (convert the SVG tray icons to PNG/ICO/ICNS via `cargo tauri icon`). Add `tauri-plugin-updater` config pointing at a `latest.json` URL.

- [ ] **Step 2:** Update `sidecar.rs` to launch via `tauri_plugin_shell::ShellExt::shell().sidecar("nemotronflow-sidecar")` (this resolves the bundled path in production) instead of the dev `python -m nemotronflow` fallback.

- [ ] **Step 3 (build + smoke):** `cargo tauri build` on Windows; run the resulting installer; launch from Start Menu; confirm the full push-to-talk loop works on a machine without Python.

- [ ] **Step 4:** Commit:
```bash
git add frontend/src-tauri
git commit -m "build(tauri): bundle pyinstaller sidecar as external binary, add updater config"
```

---

## Task M6.4: Diagnostics tray wiring + release CI

**Files:** `frontend/src-tauri/src/diagnostics.rs`, `tray.rs`, `.github/workflows/release.yml`.

- [ ] **Step 1:** `diagnostics.rs` handles the tray "Export diagnostics" item: calls an RPC `diagnostics/export` (add to the protocol + server dispatcher, calling `build_diagnostics_zip`), shows a confirm dialog (include history? checkbox), writes the zip to `~/Desktop/nemotronflow-diagnostics-<ts>.zip`, and opens the folder. No automatic upload.

- [ ] **Step 2:** `.github/workflows/release.yml` — on tag `v*.*.*`: matrix builds (Windows primary, macOS + Linux secondary), run PyInstaller then `cargo tauri build`, upload each platform's installer to the GitHub Release, and write a `latest.json` updater manifest. macOS signing gated behind `APPLE_SIGNING_IDENTITY` secret.

- [ ] **Step 3 (tag + verify):** `make lint && make test`; commit; tag `v0.1.0-m6`. Confirm the release workflow produces downloadable Windows installer artifacts.

**M6 commit boundary:** `v0.1.0-m6` — NemotronFlow installs like a commercial app. One installer, one Start Menu entry, runs from the tray, updates silently, and exports diagnostics on demand.
