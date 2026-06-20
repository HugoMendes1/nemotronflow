# M4 — SQLite history + virtualized History UI + Settings UI

**Goal:** The app remembers every transcription (SQLite, async off the hot path) and exposes a real Settings page that controls all the settings that have so far been hardcoded (hotkey, mic, auto-paste, restore mode, engine, theme). History is a virtualized, searchable list with copy/favorite/delete/export.

**Files to create:**
- `backend/nemotronflow/history.py`, `backend/nemotronflow/settings.py`
- `backend/nemotronflow/config/default_settings.json`
- `tests/test_history.py`, `tests/test_settings.py`
- `frontend/src/settings/SettingsPage.tsx`, `sections/{General,Audio,Engine,Output,History,Advanced,Dev}Tab.tsx`, `controls/{HotkeyPicker,MicDropdown,Toggle,Select,Slider}.tsx`
- `frontend/src/history/HistoryPage.tsx`, `HistoryItem.tsx`, `HistoryActions.tsx`, `HistoryEmptyState.tsx`, `useVirtualList.ts`
- `frontend/src/hooks/useSettings.ts`, `frontend/src/hooks/useHistory.ts`
- vitest tests for the hooks + a virtual-list math test
- Server handler wiring: `server.py` gains `history/*`, `settings/get`, `settings/set`, `mics/list`, `mics/test`.

**Dependencies:** M3 (orchestrator emits results that history must capture).

**Acceptance criteria:**
- Every `transcription/result` is appended to `history.db` **asynchronously** (proven: transcribe latency unaffected by a deliberately slow insert in a test).
- History schema matches spec §9 exactly: `id, timestamp, text, engine, audio_ms, inference_ms, language, favorite, tags`.
- `history/list` supports pagination + search + engine filter + favorite filter.
- `history/add/delete/edit/favorite/clear/export` all work via RPC.
- Settings persist to `~/.nemotronflow/settings.json`, validate against the schema, and changes take effect (e.g. toggling `auto_paste_enabled` changes behavior on the next utterance).
- Settings page renders all 7 tabs; changing a field persists and (where relevant) reconfigures the live process.
- History page is virtualized — rendering 10 000 items does not lag.
- The orchestrator (M1b) is refactored to read live settings instead of constructor args.

**Test strategy:** pytest for history CRUD + pagination + async-insert-is-off-hot-path + settings validation; vitest for `useVirtualList` math (windowing indices) and `useSettings` optimistic updates; component test for the settings form save flow.

**Estimated complexity:** High (breadth, not depth — lots of small UI pieces).

**Commit boundaries:** one per task, tag `v0.1.0-m4`.

---

## Task M4.1: Settings dataclass + persistence (TDD)

**Files:** `backend/nemotronflow/settings.py`, `backend/nemotronflow/config/default_settings.json`, `tests/test_settings.py`.

- [ ] **Step 1 (write failing test):** `tests/test_settings.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nemotronflow.settings import Settings


def test_defaults_match_spec() -> None:
    s = Settings()
    assert s.hotkey == "ctrl+space"
    assert s.engine == "nemotron"
    assert s.auto_paste_enabled is True
    assert s.clipboard_restore_mode == "delayed"
    assert s.clipboard_restore_delay_ms == 2500
    assert s.history_enabled is True
    assert s.theme == "dark"
    assert s.overlay_position == "bottom_center"
    assert s.vad_enabled is False
    assert s.warmup_on_startup is True
    assert s.dev_stub_engine is False


def test_save_and_load_roundtrip(tmp_data_dir: Path) -> None:
    s = Settings()
    s.auto_paste_enabled = False
    s.save()
    loaded = Settings.load()
    assert loaded.auto_paste_enabled is False


def test_load_rejects_unknown_keys(tmp_data_dir: Path) -> None:
    path = Settings.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hotkey": "ctrl+space", "bogus": 1}), encoding="utf-8")
    loaded = Settings.load()
    assert loaded.hotkey == "ctrl+space"
    assert not hasattr(loaded, "bogus")


def test_load_fills_missing_with_defaults(tmp_data_dir: Path) -> None:
    path = Settings.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hotkey": "alt+f1"}), encoding="utf-8")
    loaded = Settings.load()
    assert loaded.hotkey == "alt+f1"
    assert loaded.theme == "dark"  # default filled in


def test_set_validates_and_persists(tmp_data_dir: Path) -> None:
    s = Settings()
    s.set("auto_paste_enabled", False)
    assert Settings.load().auto_paste_enabled is False
    with pytest.raises(ValueError):
        s.set("clipboard_restore_mode", "bogus")  # invalid enum
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_settings.py -v`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/settings.py` — a `@dataclass Settings` with every field from spec §8, classmethods `load()`, `save()`, `path()` (resolving `~/.nemotronflow/settings.json` or `$NEMOTRONFLOW_DATA_DIR`), `set(key, value)` with per-field validation (enums for `clipboard_restore_mode`, `overlay_position`, `theme`, `engine`; ranges for the `_ms` ints), atomic save via `tempfile + os.replace`. The fields and defaults are mirrored to `config/default_settings.json` (generated/checked-in) and `load()` merges it as the base.

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_settings.py -v`.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/settings.py backend/nemotronflow/config tests/test_settings.py
git commit -m "feat(settings): add Settings dataclass with validation and atomic persistence"
```

---

## Task M4.2: SQLite history with async insert (TDD)

**Files:** `backend/nemotronflow/history.py`, `tests/test_history.py`.

- [ ] **Step 1 (write failing test):** `tests/test_history.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nemotronflow.history import History, HistoryEntry


@pytest.fixture
def history(tmp_data_dir: Path) -> History:
    return History(db_path=History.default_path())


@pytest.mark.asyncio
async def test_add_returns_id_and_is_listable(history: History) -> None:
    await history.start()
    hid = await history.add(HistoryEntry(text="hello", engine="stub", audio_ms=1000, inference_ms=50))
    rows = await history.list(limit=10)
    assert len(rows) == 1
    assert rows[0].id == hid
    assert rows[0].text == "hello"
    await history.stop()


@pytest.mark.asyncio
async def test_add_is_async_and_off_hot_path(history: History) -> None:
    """Inserts must not block the caller even under contention."""
    await history.start()
    t0 = asyncio.get_event_loop().time()
    # Fire 50 inserts without awaiting the worker; the caller returns immediately.
    for i in range(50):
        await history.add(HistoryEntry(text=f"t{i}", engine="stub", audio_ms=10, inference_ms=1))
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 0.05  # 50 enqueues well under 50ms; DB writes happen in worker
    await history.drain()  # wait for worker
    rows = await history.list(limit=100)
    assert len(rows) == 50
    await history.stop()


@pytest.mark.asyncio
async def test_search_filter_favorite_pagination(history: History) -> None:
    await history.start()
    a = await history.add(HistoryEntry(text="docker compose", engine="nemotron", audio_ms=10, inference_ms=1))
    await history.add(HistoryEntry(text="react component", engine="nemotron", audio_ms=10, inference_ms=1))
    await history.favorite(a, True)
    favs = await history.list(limit=10, favorite_only=True)
    assert len(favs) == 1 and favs[0].id == a
    searched = await history.list(limit=10, search="docker")
    assert len(searched) == 1
    await history.stop()


@pytest.mark.asyncio
async def test_edit_delete_clear_export(history: History, tmp_data_dir: Path) -> None:
    await history.start()
    hid = await history.add(HistoryEntry(text="original", engine="stub", audio_ms=10, inference_ms=1))
    await history.edit(hid, "edited")
    assert (await history.list(limit=10))[0].text == "edited"
    await history.delete(hid)
    assert (await history.list(limit=10)) == []
    await history.add(HistoryEntry(text="x", engine="stub", audio_ms=1, inference_ms=1))
    path = await history.export("md")
    assert path.exists() and path.read_text(encoding="utf-8").strip().endswith("x")
    await history.clear()
    assert (await history.list(limit=10)) == []
    await history.stop()
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_history.py -v`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/history.py` — `HistoryEntry` dataclass, `History` class with `aiosqlite`, an internal `asyncio.Queue` + worker task: `add()` only enqueues (returns a generated id immediately), the worker drains to the DB. `list()` supports `limit, offset, search, engine, favorite_only`. `edit/delete/favorite/clear/export(format)` run directly. `export` writes to `~/.nemotronflow/exports/history-<ts>.{json|md|csv}`.

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_history.py -v`.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/history.py tests/test_history.py
git commit -m "feat(history): add SQLite history with async worker off the hot path"
```

---

## Task M4.3: Wire history + settings into server + orchestrator

**Files:** modify `backend/nemotronflow/server.py`, `backend/nemotronflow/transcription.py`.

- [ ] **Step 1:** In `server.py`, replace `_default_settings()` with the real `Settings`. Register handlers: `settings/get`, `settings/set` (validate + save + emit `settings/changed`), `history/list/add/delete/edit/favorite/clear/export`, `mics/list` (sounddevice query), `mics/test`.

- [ ] **Step 2:** In the orchestrator (M1b), on every `transcription/result`, **enqueue a history insert** with the result's fields. Refactor the orchestrator to read `auto_paste_enabled`, `llm_*` from the live `Settings` instance rather than constructor args (so setting changes take effect immediately).

- [ ] **Step 3 (integration test):** extend `tests/test_transcription.py` — after a press/release, assert a row exists in a temp history DB.

- [ ] **Step 4 (run + commit):**
```bash
make lint && make test
git add -A
git commit -m "feat(server): wire real settings and history into dispatcher and orchestrator"
```

---

## Task M4.4: `useVirtualList` + `useSettings` + `useHistory` hooks (TDD)

**Files:** `frontend/src/history/useVirtualList.ts`, `frontend/src/hooks/useSettings.ts`, `frontend/src/hooks/useHistory.ts`, plus vitest tests.

- [ ] **Step 1 (write failing test):** `frontend/src/__tests__/history/useVirtualList.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { computeWindow } from "../../history/useVirtualList";

describe("computeWindow", () => {
  it("returns the visible index range for a scroll position", () => {
    // 50 items, 40px each, viewport 200px → 5 visible, overscan 2 → 9 in DOM
    const w = computeWindow({ total: 50, itemHeight: 40, viewportHeight: 200, scrollTop: 80, overscan: 2 });
    expect(w.startIndex).toBe(0); // scrollTop 80 → item 2, minus overscan clamps to 0
    expect(w.endIndex).toBe(8);   // 5 visible + 2 overscan each side
    expect(w.offsetY).toBe(0);
  });

  it("clamps at the end", () => {
    const w = computeWindow({ total: 50, itemHeight: 40, viewportHeight: 200, scrollTop: 1800, overscan: 2 });
    expect(w.endIndex).toBe(50);
  });
});
```

- [ ] **Step 2 (run, confirm fail):** `pnpm -C frontend test`.

- [ ] **Step 3 (implement):** `computeWindow` is a pure function (the windowing math, testable without React). `useVirtualList` wraps it with a scroll listener + `useState`. `useSettings` loads via `settings/get`, exposes `{ settings, update(key, value) }` with optimistic local state + `settings/set` RPC + revert on error. `useHistory` exposes `{ items, loadMore, search, setFilter, mutate }`.

- [ ] **Step 4 (run, confirm pass):** `pnpm -C frontend test`.

- [ ] **Step 5:** Commit:
```bash
git add frontend/src/history frontend/src/hooks frontend/src/__tests__/history frontend/src/__tests__/hooks
git commit -m "feat(hooks): add virtualization, settings, and history hooks with tests"
```

---

## Task M4.5: Settings page (7 tabs) + History page

**Files:** all `frontend/src/settings/**` and `frontend/src/history/**` components listed in the milestone header.

- [ ] **Step 1:** Build the tiny shared controls (`Toggle`, `Select`, `Slider`, `HotkeyPicker`, `MicDropdown`) — each is ~30 lines, wraps a native input styled with design tokens. `HotkeyPicker` captures the next keypress and renders `ctrl+space`. `MicDropdown` calls `mics/list` + `mics/test`.

- [ ] **Step 2:** `SettingsPage.tsx` — tab bar + the 7 tab components. Each tab binds to `useSettings`. Saving a field calls `update(key, value)` immediately (debounced 300 ms for text inputs). The tabs cover every Settings field per spec §8 (General: hotkey, theme, tray, startup, overlay position/offsets; Audio: mic, gain, normalize, resample, VAD flags; Engine: engine select, checkpoint path, device, precision, batch; Output: auto_paste, delay, restore mode, restore delay; History: enable, retention, export, clear; Advanced: warmup_on_startup, engine_timeout_ms, precision, batch_size, future_download_models, device override; Dev: stub toggle, log level).

- [ ] **Step 3:** `HistoryPage.tsx` — search bar, filter chips (engine, favorite), virtualized list via `useVirtualList`, each `HistoryItem` shows timestamp · text · engine · audio_ms with `HistoryActions` (copy, favorite, edit, delete). `HistoryEmptyState` when empty.

- [ ] **Step 4 (component test):** `frontend/src/__tests__/settings/SettingsPage.test.tsx` — render the page, change a toggle, assert `settings/set` RPC was called with the right payload (mock the RPC client).

- [ ] **Step 5:** Commit:
```bash
git add frontend/src/settings frontend/src/history frontend/src/__tests__/settings
git commit -m "feat(ui): add settings (7 tabs) and virtualized history pages"
```

---

## Task M4.6: Manual acceptance + tag

- [ ] **Step 1 (manual):** Open Settings, change hotkey to `alt+f1`, confirm the new combo triggers push-to-talk. Toggle auto-paste off, confirm overlay shows `✓ Copied`. Generate 20 utterances, confirm History lists/searches/filters them, and copy/favorite/delete/export all work. Switch theme to light, confirm tokens update.

- [ ] **Step 2 (full suite):** `make lint && make test`.

- [ ] **Step 3:** Commit + tag:
```bash
git add -A
git commit -m "feat(m4): settings + history complete (manual acceptance passed)"
git tag v0.1.0-m4
```

**M4 commit boundary:** `v0.1.0-m4` — a complete, configurable app with memory. Every transcription is saved and searchable; every setting is user-controllable and takes effect live. Only the speech engine is still a stub.
