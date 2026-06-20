# M2 — Tauri 3-window shell + overlay (5 states) + tray + WS bridge

**Goal:** The user-facing app exists. Tauri launches the Python sidecar, connects over WebSocket, and renders the floating overlay (click-through, always-on-top, never-activating) that transitions through all 5 states. Tray icon mirrors state. Press Ctrl+Space → overlay shows `Listening`; release → `Processing` → `Done`. Paste is stubbed (M3); this milestone proves the visual + IPC loop end-to-end.

**Files to create:**
- `frontend/src-tauri/Cargo.toml`, `tauri.conf.json`, `build.rs`, `icons/`
- `frontend/src-tauri/src/main.rs`, `sidecar.rs`, `rpc.rs`, `tray.rs`, `overlay.rs`, `logging.rs`
- `frontend/src/App.tsx` (router), `frontend/src/rpc/client.ts`, `frontend/src/rpc/events.ts`
- `frontend/src/hooks/useBackend.ts`, `frontend/src/hooks/useBackendEvent.ts`, `frontend/src/hooks/useEngineStatus.ts`
- `frontend/src/overlay/Overlay.tsx`, `OverlayStates.tsx`, `LevelMeter.tsx`, `overlay.css`
- `frontend/src/assets/tray-icons/{idle,listening,processing,done}.svg`
- `frontend/src/__tests__/rpc/client.test.ts`, `frontend/src/__tests__/overlay/Overlay.test.tsx`, `frontend/src/__tests__/hooks/useBackend.test.ts`

**Dependencies:** M1b (sidecar emits `state/changed` + `transcription/result`). Rust toolchain installed.

**Acceptance criteria:**
- `cargo tauri dev` boots the app: spawns the Python sidecar, reads the READY line, connects the WS client, shows a tray icon.
- The overlay is transparent, click-through, always-on-top, and **never steals focus** (verified: clicking it leaves focus on the previously-active window).
- Overlay renders all 5 states (`idle`/`listening`/`processing`/`done`/`error`) with the spec'd animation durations.
- Real Ctrl+Space drives the loop: overlay shows Listening → Processing → Done in response to the sidecar's notifications.
- WebSocket reconnect on drop (500 ms × 20) with a tray notification; no modal dialogs.
- Overlay dynamic width 280–420 px; CPU/CUDA badge shows (placeholder `CPU MODE` until M5).

**Test strategy:** vitest for the RPC client (mocked WebSocket) + overlay state machine; `cargo test` for sidecar.rs READY-line parsing and rpc.rs reconnect logic. Manual smoke test for window focus behavior (documented in the task).

**Estimated complexity:** High. Window-platform flags + WS client lifecycle are the bulk.

**Commit boundaries:** one per task, tag `v0.1.0-m2`.

---

## Task M2.1: Tauri project init + Cargo deps + config

**Files:** `frontend/src-tauri/Cargo.toml`, `tauri.conf.json`, `build.rs`, `icons/`, `src/main.rs` (trivial).

- [ ] **Step 1:** Scaffold the Tauri project inside `frontend/`:
```bash
cd frontend
cargo tauri init
```
Answer the prompts: app name `NemotronFlow`, window title empty, frontend dev URL `http://localhost:5173`, frontend dist `../dist`, dev command `pnpm dev`, build command `pnpm build`. This generates `src-tauri/` with a baseline config.

- [ ] **Step 2:** Edit `frontend/src-tauri/Cargo.toml` to add dependencies:
```toml
[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
tauri-plugin-shell = "2"
tokio = { version = "1", features = ["full"] }
tokio-tungstenite = { version = "0.23", features = ["connect"] }
futures-util = "0.3"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
arboard = "3"
enigo = "0.2"
anyhow = "1"
thiserror = "1"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
directories = "5"

[build-dependencies]
tauri-build = { version = "2", features = [] }
```

- [ ] **Step 3:** Edit `frontend/src-tauri/tauri.conf.json` — set `app.windows` to declare the **overlay** as the main window: `transparent: true`, `decorations: false`, `alwaysOnTop: true`, `skipTaskbar: true`, `resizable: false`, `focus: false`, `width: 280`, `height: 56`, `url: "index.html#/overlay"`. Set `app.security.csp` to allow the dev WebSocket. Add `tauri-plugin-shell` to plugins and declare the sidecar in `bundle.externalBin`.

- [ ] **Step 4:** Verify `cargo tauri dev` opens an empty transparent window. Commit:
```bash
git add frontend/src-tauri
git commit -m "feat(tauri): scaffold tauri v2 shell with overlay window config"
```

---

## Task M2.2: Sidecar spawner + READY-line parsing (TDD)

**Files:** `frontend/src-tauri/src/sidecar.rs`, `frontend/src-tauri/src/main.rs` (wire-in).

- [ ] **Step 1 (write failing test):** `frontend/src-tauri/src/sidecar.rs` includes a pure parser, tested with `cargo test`:

```rust
pub fn parse_ready_line(line: &str) -> Option<u16> {
    line.trim()
        .strip_prefix("NEMOTRONFLOW_READY ")
        .and_then(|p| p.trim().parse::<u16>().ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ready_line() {
        assert_eq!(parse_ready_line("NEMOTRONFLOW_READY 53124\n"), Some(53124));
    }

    #[test]
    fn rejects_garbage() {
        assert_eq!(parse_ready_line("listening on 8080"), None);
        assert_eq!(parse_ready_line(""), None);
    }
}
```

- [ ] **Step 2 (run, confirm pass — tests are inline):**
```bash
cd frontend/src-tauri && cargo test sidecar
```

- [ ] **Step 3 (implement spawner with auto-restart):** add `spawn_sidecar()` to `sidecar.rs` using `tauri_plugin_shell::process::Command` to launch `nemotronflow-sidecar` (resolved via Tauri's sidecar API), reading stdout line-by-line until a READY line appears, returning the port. In dev mode (when the sidecar binary isn't built yet), fall back to spawning `python -m nemotronflow` if `NEMOTRONFLOW_DEV=1`.

**Crash auto-restart (spec §11):** The spawner runs in a tokio task that monitors the child process. If the sidecar exits unexpectedly (non-zero code or unclean termination), the spawner:
1. Waits 500 ms.
2. Re-spawns the sidecar.
3. Reads the new READY line.
4. Reconnects the RPC client.
5. Emits a `state/changed` notification with `state: "error"`, then back to `"idle"` so the overlay briefly shows `⚠ Backend restarted` (auto-hides after 1500 ms).
This loop runs indefinitely; a manual "Quit" from the tray sends `shutdown` then kills the child. No modal dialogs on crash.

- [ ] **Step 4:** Commit:
```bash
git add frontend/src-tauri/src/sidecar.rs frontend/src-tauri/src/main.rs
git commit -m "feat(sidecar): spawn python sidecar and parse READY line for port handoff"
```

---

## Task M2.3: WebSocket RPC client with reconnect (TDD)

**Files:** `frontend/src-tauri/src/rpc.rs`.

- [ ] **Step 1 (write failing test):** inline test in `rpc.rs` for the reconnect backoff math:

```rust
pub fn next_reconnect_delay(attempt: u32) -> std::time::Duration {
    // 500ms × attempt, capped at 10s; give up after 20 attempts (caller checks).
    let ms = (500u64 * attempt as u64).min(10_000);
    std::time::Duration::from_millis(ms)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn backoff_grows_then_caps() {
        assert_eq!(next_reconnect_delay(1), Duration::from_millis(500));
        assert_eq!(next_reconnect_delay(20), Duration::from_millis(10_000));
        assert_eq!(next_reconnect_delay(50), Duration::from_millis(10_000));
    }
}
```

- [ ] **Step 2 (run, confirm pass):** `cargo test rpc`.

- [ ] **Step 3 (implement client):** `RpcClient` struct with `connect(port)`, a tokio task reading frames and dispatching to a handler callback, `send_request(method, params) -> Response`, and a reconnect loop: on disconnect, retry `next_reconnect_delay(attempt)` up to 20 times, emitting a `Disconnected`/`Reconnected` event to Tauri (via `app.emit`). Use `tracing` for logging.

- [ ] **Step 4:** Commit:
```bash
git add frontend/src-tauri/src/rpc.rs
git commit -m "feat(rpc): add WebSocket JSON-RPC client with exponential reconnect"
```

---

## Task M2.4: Overlay window platform flags (click-through + never-activate)

**Files:** `frontend/src-tauri/src/overlay.rs`, `main.rs`.

This is the make-or-break task for paste working. See spec §7.2.

- [ ] **Step 1:** Write `overlay.rs` with `apply_no_activate(window)`:
```rust
#[cfg(target_os = "windows")]
fn apply_no_activate(window: &tauri::WebviewWindow) -> anyhow::Result<()> {
    use tauri::Manager;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_NOACTIVATE,
        WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT,
    };
    let hwnd = window.hwnd()?;
    unsafe {
        let mut ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        ex |= (WS_EX_NOACTIVATE.0 | WS_EX_TRANSPARENT.0 | WS_EX_TOOLWINDOW.0 | WS_EX_LAYERED.0) as isize;
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex);
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn apply_no_activate(_window: &tauri::WebviewWindow) -> anyhow::Result<()> {
    // TODO(M2 macOS): NSWindow ignoresMouseEvents=YES + non-activating level via objc2.
    Ok(())
}

#[cfg(target_os = "linux")]
fn apply_no_activate(_window: &tauri::WebviewWindow) -> anyhow::Result<()> {
    // TODO(M2 linux): override-redirect / layer-shell KeyboardInteractivity::None.
    Ok(())
}

pub fn setup(app: &tauri::AppHandle) -> anyhow::Result<()> {
    use tauri::Manager;
    if let Some(window) = app.get_webview_window("overlay") {
        apply_no_activate(&window)?;
    }
    Ok(())
}
```
(Add `windows = { version = "0.58", features = ["Win32_UI_WindowsAndMessaging"] }` to Cargo.toml under `[target.'cfg(windows)'.dependencies]`.)

- [ ] **Step 2 (manual smoke test — document in commit msg):** With the app running, focus Notepad, press Ctrl+Space, confirm the overlay appears but Notepad **keeps** the caret. This is the acceptance check for the no-activate flag; there's no clean automated way to assert focus on all OSes, so it's a documented manual gate.

- [ ] **Step 3:** Commit:
```bash
git add frontend/src-tauri/src/overlay.rs frontend/src-tauri/src/main.rs frontend/src-tauri/Cargo.toml
git commit -m "feat(overlay): apply WS_EX_NOACTIVATE+TRANSPARENT on windows (manual focus smoke test passed)"
```

---

## Task M2.5: TS RPC client + hooks (TDD)

**Files:** `frontend/src/rpc/client.ts`, `frontend/src/rpc/events.ts`, `frontend/src/hooks/useBackend.ts`, `frontend/src/hooks/useBackendEvent.ts`, `frontend/src/hooks/useEngineStatus.ts`, plus vitest tests.

- [ ] **Step 1 (write failing test):** `frontend/src/__tests__/rpc/client.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";
import { RpcClient } from "../../rpc/client";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  send = vi.fn((data: string) => {
    // echo back a canned response for settings/get
    const msg = JSON.parse(data);
    if (msg.method === "settings/get") {
      this.onmessage?.({ data: JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { hotkey: "ctrl+space" } }) });
    }
  });
  close = vi.fn();
  constructor(public url: string) { FakeWebSocket.instances.push(this); }
}

describe("RpcClient", () => {
  it("sends a request and resolves the response", async () => {
    const client = new RpcClient("ws://x", (url) => new FakeWebSocket(url) as unknown as WebSocket);
    const result = await client.request("settings/get", {});
    expect(result).toEqual({ hotkey: "ctrl+space" });
  });
});
```

- [ ] **Step 2 (run, confirm fail):** `pnpm -C frontend test`.

- [ ] **Step 3 (implement):** `frontend/src/rpc/client.ts` — `RpcClient` with an injectable WebSocket factory (so tests pass `FakeWebSocket`), a monotonic request id counter, a pending-requests map, `request()` returning a typed Promise, `onNotification(method, cb)` subscription, and `close()`.

- [ ] **Step 4 (implement hooks):**
  - `useBackend.ts` — manages a singleton `RpcClient`, exposes `{ status: "connecting"|"connected"|"reconnecting"|"error" }`, reconnect logic.
  - `useBackendEvent.ts` — subscribes to a notification method, returns latest payload + a reset.
  - `useEngineStatus.ts` — wraps `useBackendEvent("model/status")`, returns `{ phase, engine, device }`.

- [ ] **Step 5 (run, confirm pass):** `pnpm -C frontend test`.

- [ ] **Step 6:** Commit:
```bash
git add frontend/src/rpc frontend/src/hooks frontend/src/__tests__
git commit -m "feat(rpc): add typed TS JSON-RPC client and React hooks with reconnect"
```

---

## Task M2.6: Overlay React component + 5-state machine (TDD)

**Files:** `frontend/src/overlay/Overlay.tsx`, `OverlayStates.tsx`, `LevelMeter.tsx`, `overlay.css`, `frontend/src/__tests__/overlay/Overlay.test.tsx`.

- [ ] **Step 1 (write failing test):** `frontend/src/__tests__/overlay/Overlay.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Overlay } from "../../overlay/Overlay";

describe("Overlay", () => {
  it("renders nothing when idle", () => {
    const { container } = render(<Overlay state="idle" device="cpu" />);
    expect(container.firstChild).toBeNull();
  });

  it("renders listening state", () => {
    render(<Overlay state="listening" device="cpu" />);
    expect(screen.getByText(/listening/i)).toBeInTheDocument();
  });

  it("renders done state with pasted/copied label", () => {
    render(<Overlay state="done" device="cuda" autoPaste={true} />);
    expect(screen.getByText(/pasted/i)).toBeInTheDocument();
  });

  it("renders error state with message", () => {
    render(<Overlay state="error" device="cpu" errorMessage="Backend Disconnected" />);
    expect(screen.getByText(/backend disconnected/i)).toBeInTheDocument();
  });

  it("shows CPU MODE badge when device is cpu", () => {
    render(<Overlay state="listening" device="cpu" />);
    expect(screen.getByText(/cpu mode/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2 (run, confirm fail):** `pnpm -C frontend test`.

- [ ] **Step 3 (implement):** `Overlay.tsx` switches on the `state` prop, rendering the corresponding `OverlayStates` sub-component or `null` for idle. `overlay.css` implements the spec'd animations (listening fade 120 ms, processing crossfade 160 ms, success hold 600 ms, error hold 1500 ms, hide 180 ms) and dynamic width (min 280 / max 420 px). `LevelMeter.tsx` is an rAF-driven bar fed by throttled RMS values from the `state/changed` payload (no-op for v1 when no RMS, but the component exists). The CPU/CUDA badge renders based on the `device` prop.

- [ ] **Step 4 (run, confirm pass):** `pnpm -C frontend test`.

- [ ] **Step 5:** Commit:
```bash
git add frontend/src/overlay frontend/src/__tests__/overlay
git commit -m "feat(overlay): add 5-state overlay with spec animations and device badge"
```

---

## Task M2.7: Tray icon + menu

**Files:** `frontend/src-tauri/src/tray.rs`, `main.rs`, `frontend/src/assets/tray-icons/*.svg`.

- [ ] **Step 1:** Write `tray.rs`: builds a `TrayIconBuilder` with an idle icon, a menu (`Toggle listening`, `Settings…`, `History…`, `Engine ▸`, `Restart backend`, `Reload engine`, `Open logs`, `Export diagnostics`, `Pause hotkey`, `Quit`). Menu items that need later milestones (`Export diagnostics`, `Reload engine`) are present but disabled. Icon swaps on `state/changed`: idle→`idle.svg`, listening→`listening.svg` (red), processing→`processing.svg`, done→`done.svg` (400 ms green flash then back to idle).

- [ ] **Step 2:** Wire tray events: `Settings…`/`History…` open their respective Tauri windows (`WebviewWindowBuilder` with the `#/settings` / `#/history` URL, normal focus, not always-on-top). `Toggle listening` is wired in M2.8; `Quit` calls `app.exit(0)` after sending `shutdown` to the sidecar.

- [ ] **Step 3:** Commit:
```bash
git add frontend/src-tauri/src/tray.rs frontend/src-tauri/src/main.rs frontend/src/assets/tray-icons
git commit -m "feat(tray): add tray icon with state colors and menu"
```

---

## Task M2.8: End-to-end wiring + manual acceptance

**Files:** `frontend/src/App.tsx` (router), `frontend/src/overlay/Overlay.tsx` (wire to events).

- [ ] **Step 1:** `App.tsx` becomes a `HashRouter` that renders `<Overlay/>` at `#/overlay`, and placeholder settings/history pages at their routes (real UI in M4).

- [ ] **Step 2:** `Overlay.tsx` reads `state` and `device` from `useBackendEvent("state/changed")` + `useEngineStatus()` instead of props. The container overlay is driven entirely by backend events.

- [ ] **Step 3 (manual acceptance — record in commit body):**
  - `cargo tauri dev` boots, sidecar connects, tray appears.
  - Focus a text editor, press Ctrl+Space, hold, speak — overlay shows `Listening` with the mic pulse.
  - Release — overlay crossfades to `Processing` then `✓ Copied` (paste is M3; for now text only lands on clipboard in M3, so this is "copied" semantically — but since M2 doesn't implement clipboard either, the overlay just shows Done without affecting the target app. That's fine; M3 closes the loop).
  - Overlay stays click-through; focus never leaves the editor.
  - Kill the sidecar process — overlay shows `✖ Backend Disconnected`, tray reconnects.

- [ ] **Step 4 (run full test suite):**
```bash
make lint && make test
```

- [ ] **Step 5:** Commit + tag:
```bash
git add frontend/src/App.tsx frontend/src/overlay/Overlay.tsx
git commit -m "feat(app): wire overlay to backend events end-to-end (M2 manual acceptance passed)"
git tag v0.1.0-m2
```

**M2 commit boundary:** `v0.1.0-m2` — a real, runnable desktop app. The overlay reacts to a real Ctrl+Space through the whole stack: pynput → ring buffer → StubASR → WebSocket → Tauri → React overlay. It looks and feels like Wispr Flow visually. The only missing piece is the actual paste, which M3 adds.
