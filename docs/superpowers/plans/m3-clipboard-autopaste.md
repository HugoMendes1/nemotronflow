# M3 — Clipboard backup + auto-paste + restore modes (Rust)

**Goal:** Close the loop. When the backend emits `transcription/result`, Tauri puts the text on the clipboard and simulates Ctrl+V into the focused app, then restores the user's prior clipboard per the configured restore mode. After M3, holding Ctrl+Space and speaking makes the text appear in ChatGPT/Cursor/VSCode/Obsidian exactly like Wispr Flow.

**Files to create:**
- `frontend/src-tauri/src/clipboard.rs` — arboard wrapper: read, set, backup/restore.
- `frontend/src-tauri/src/autopaste.rs` — enigo Ctrl+V + restore-mode scheduling.
- `frontend/src/__tests__/` — n/a (Rust tests live in `src-tauri`).
- `frontend/src-tauri/src/clipboard.rs` inline `#[cfg(test)]` modules.
- `tests/benchmarks/test_paste_latency.py` — stub-only paste timing.

**Dependencies:** M2 (rpc.rs delivers `transcription/result`).

**Acceptance criteria:**
- On `transcription/result` with `auto_paste: true`: backup clipboard → set text → Ctrl+V → schedule restore per mode.
- Restore modes work: `immediate` (restore right after paste), `delayed` (restore after 2500 ms, async, off the critical path), `disabled` (leave transcript on clipboard).
- `auto_paste: false`: set clipboard only, no Ctrl+V, overlay shows `✓ Copied`.
- The paste target is whatever had focus before the overlay (guaranteed by M2's no-activate window).
- No modal dialogs; failures (clipboard locked) are logged + show a brief `✖` overlay state.

**Test strategy:** `cargo test` for clipboard backup/restore sequencing and restore-mode scheduling (with a mockable clock/clipboard via trait). The actual OS paste is a manual smoke test (documented). A Python benchmark measures the IPC-to-clipboard-set round trip on StubASR.

**Estimated complexity:** Medium. The restore-mode scheduling and "don't clobber the user's clipboard" logic need care.

**Commit boundaries:** one per task, tag `v0.1.0-m3`.

---

## Task M3.1: Clipboard wrapper with backup/restore (TDD)

**Files:** `frontend/src-tauri/src/clipboard.rs`.

- [ ] **Step 1 (write failing test):** inline test module using a trait so the real `arboard::Clipboard` is swapped for a fake:

```rust
pub trait ClipboardBackend: Send + 'static {
    fn get_text(&self) -> anyhow::Result<Option<String>>;
    fn set_text(&mut self, text: &str) -> anyhow::Result<()>;
}

pub struct Clipboard<B: ClipboardBackend> {
    backend: B,
    backup: Option<String>,
}

impl<B: ClipboardBackend> Clipboard<B> {
    pub fn new(backend: B) -> Self { Self { backend, backup: None } }
    pub fn backup(&mut self) -> anyhow::Result<()> {
        self.backup = self.backend.get_text()?;
        Ok(())
    }
    pub fn set_text(&mut self, text: &str) -> anyhow::Result<()> {
        self.backend.set_text(text)
    }
    pub fn restore(&mut self) -> anyhow::Result<()> {
        match self.backup.take() {
            Some(prev) => self.backend.set_text(&prev),
            None => Ok(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    struct FakeBackend { text: Rc<RefCell<Option<String>>> }
    impl ClipboardBackend for FakeBackend {
        fn get_text(&self) -> anyhow::Result<Option<String>> { Ok(self.text.borrow().clone()) }
        fn set_text(&mut self, text: &str) -> anyhow::Result<()> {
            *self.text.borrow_mut() = Some(text.to_string()); Ok(())
        }
    }

    #[test]
    fn backup_then_restore_preserves_prior_clipboard() {
        let shared = Rc::new(RefCell::new(Some("user's old copy".into())));
        let mut cb = Clipboard::new(FakeBackend { text: shared.clone() });
        cb.backup().unwrap();
        cb.set_text("transcript").unwrap();
        assert_eq!(*shared.borrow(), Some("transcript".into()));
        cb.restore().unwrap();
        assert_eq!(*shared.borrow(), Some("user's old copy".into()));
    }

    #[test]
    fn restore_without_backup_is_noop() {
        let shared = Rc::new(RefCell::new(None));
        let mut cb = Clipboard::new(FakeBackend { text: shared.clone() });
        cb.set_text("x").unwrap();
        cb.restore().unwrap(); // no panic
        assert_eq!(*shared.borrow(), Some("x".into()));
    }
}

pub struct ArboardBackend(arboard::Clipboard);
impl ClipboardBackend for ArboardBackend {
    fn get_text(&self) -> anyhow::Result<Option<String>> {
        Ok(self.0.get_text().ok())
    }
    fn set_text(&mut self, text: &str) -> anyhow::Result<()> {
        self.0.set_text(text).map_err(|e| anyhow::anyhow!(e))
    }
}
```

- [ ] **Step 2 (run, confirm pass):** `cd frontend/src-tauri && cargo test clipboard`.

- [ ] **Step 3:** Commit:
```bash
git add frontend/src-tauri/src/clipboard.rs
git commit -m "feat(clipboard): add testable clipboard wrapper with backup/restore"
```

---

## Task M3.2: Auto-paste with restore modes (TDD)

**Files:** `frontend/src-tauri/src/autopaste.rs`.

- [ ] **Step 1 (write failing test):** inline test module for the restore-mode scheduler. The scheduler is a pure function returning a `RestoreAction` so it's testable without a real timer:

```rust
#[derive(Debug, PartialEq)]
pub enum RestoreAction {
    Immediate,
    Delayed(std::time::Duration),
    Disabled,
}

pub fn plan_restore(mode: &str, delay_ms: u64) -> anyhow::Result<RestoreAction> {
    match mode {
        "immediate" => Ok(RestoreAction::Immediate),
        "delayed" => Ok(RestoreAction::Delayed(std::time::Duration::from_millis(delay_ms))),
        "disabled" => Ok(RestoreAction::Disabled),
        other => Err(anyhow::anyhow!("unknown restore mode: {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plan_restore_modes() {
        assert_eq!(plan_restore("immediate", 0).unwrap(), RestoreAction::Immediate);
        assert_eq!(plan_restore("delayed", 2500).unwrap(),
                   RestoreAction::Delayed(std::time::Duration::from_millis(2500)));
        assert_eq!(plan_restore("disabled", 0).unwrap(), RestoreAction::Disabled);
    }

    #[test]
    fn plan_restore_rejects_unknown() {
        assert!(plan_restore("bogus", 0).is_err());
    }
}
```

- [ ] **Step 2 (run, confirm pass):** `cargo test autopaste`.

- [ ] **Step 3 (implement paste orchestration):** `pub async fn paste_text(clipboard, enigo, text, auto_paste, paste_delay_ms, restore_action)`:
  - `clipboard.backup()?`
  - `clipboard.set_text(&text)?`
  - if `auto_paste`: `sleep(paste_delay_ms)`; `enigo.key_down(Ctrl)`; `enigo.key_click(V)`; `enigo.key_up(Ctrl)`; `sleep(40ms)` (let paste land).
  - match `restore_action`:
    - `Immediate` → `clipboard.restore()?`
    - `Delayed(d)` → `tokio::spawn` a task that sleeps `d` then restores (off the critical path — function returns immediately).
    - `Disabled` → do nothing.
  - Errors are returned to the caller which logs + emits an error overlay state.

- [ ] **Step 4:** Commit:
```bash
git add frontend/src-tauri/src/autopaste.rs
git commit -m "feat(autopaste): add enigo Ctrl+V with configurable restore modes"
```

---

## Task M3.3: Wire paste into the RPC event handler + manual acceptance

**Files:** `frontend/src-tauri/src/rpc.rs` (handler), `main.rs` (own `Clipboard` + `Enigo` instances, pass to handler).

- [ ] **Step 1:** In `rpc.rs`, register an `on_notification("transcription/result")` handler that calls `autopaste::paste_text(...)` with the current settings (`auto_paste_enabled`, `auto_paste_delay_ms`, `clipboard_restore_mode`, `clipboard_restore_delay_ms`) — settings arrive via `settings/get` on connect and are cached in app state.

- [ ] **Step 2 (manual acceptance — document in commit):**
  - Focus a browser ChatGPT input, hold Ctrl+Space, say "hello world this is a test", release.
  - Confirm the StubASR text (`[stub] the quick brown fox…`) pastes into ChatGPT within the latency budget.
  - Confirm your prior clipboard content returns ~2.5 s later.
  - Toggle `auto_paste_enabled` off (hardcode in settings for M3; UI in M4), repeat — confirm text lands only on the clipboard, overlay shows `✓ Copied`.

- [ ] **Step 3 (paste latency benchmark):** `tests/benchmarks/test_paste_latency.py` measures the IPC round trip on the StubASR path using a fake RPC client (the real paste is OS-bound). Asserts the Tauri-side clipboard.set + enigo dispatch completes in < 20 ms.

- [ ] **Step 4 (run full suite + tag):**
```bash
make lint && make test
git add -A
git commit -m "feat(paste): wire transcription/result to clipboard+autopaste (M3 manual acceptance passed)"
git tag v0.1.0-m3
```

**M3 commit boundary:** `v0.1.0-m3` — **a fully working Wispr-Flow-equivalent dev loop.** Hold Ctrl+Space, speak, release, and the StubASR text appears in any focused app with automatic clipboard restore. The only thing not real is the speech (StubASR returns canned text). M5 swaps in Nemotron. This is the moment the product demo works.
