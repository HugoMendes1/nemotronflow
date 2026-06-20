# NemotronFlow — Design Specification

- **Date:** 2026-06-19
- **Status:** Approved (design phase complete)
- **License:** MIT
- **Target release:** Alpha after M7

## 1. Overview

NemotronFlow is an open-source desktop application that provides a premium push-to-talk speech-to-text experience, behaviorally indistinguishable from Wispr Flow / WhisperFlow, powered by NVIDIA Nemotron 3.5 ASR.

The user presses and holds **Ctrl + Space**. While held, audio is captured continuously and a small floating overlay reads `🎤 Listening…`. On release, audio is transcribed, the text is placed on the clipboard, and (optionally) pasted into the currently focused application.

**Supported paste targets:** any application that accepts keyboard input — ChatGPT, Cursor, Claude, VSCode, Obsidian, browsers, and terminal windows.

### Product philosophy

- **Performance > Features.** Latency is the primary product feature.
- **Local First.** No telemetry, no analytics, no crash reporting, no cloud dependencies unless explicitly opted in.
- **Minimal UI, Premium UX.** Dark-mode-first, rounded corners, smooth animations, fast startup, low latency.
- **Open Source.** MIT licensed, commercial-quality experience, no subscriptions.
- **No Electron.** The shell is Tauri (Rust + WebView).

### Non-goals for v1

- Streaming partial transcripts (reserved in the protocol; not wired).
- Voice activity detection (flags reserved; not wired).
- LLM cleanup (file present; identity transform).
- Future ASR engines (WhisperASR, ParakeetASR, DeepgramASR, OpenAIRealtimeASR, GeminiLiveASR) — the plugin contract supports them; v1 ships Nemotron + Stub only.

## 2. Architecture

### 2.1 Process model: Sidecar

A single Tauri (Rust) process launches and supervises a single long-lived Python sidecar. The two communicate over **WebSocket** (JSON-RPC 2.0) on localhost. There is no per-utterance process spawning.

```
┌─────────────────────────────┐     WebSocket + JSON-RPC      ┌─────────────────────────────┐
│      Tauri (Rust) shell     │   ◀──────────────────────────▶│     Python sidecar          │
│                             │                                │                             │
│  • Overlay window           │   Notifications (Python→Tauri) │  • hotkeys.py (pynput)      │
│  • Window lifecycle         │     state/changed              │  • audio_capture.py         │
│  • Tray icon                │     transcription/result       │  • transcription.py         │
│  • Settings UI (React)      │     transcription/partial *    │  • asr/ (BaseASR + engines) │
│  • History UI (React)       │     vad/start, vad/end *       │  • history.py               │
│  • Clipboard integration    │     model/progress             │  • settings.py              │
│  • Auto-paste               │     download/progress          │  • llm_cleanup.py           │
│  • System notifications     │     log, error                 │  • ipc/ (server + protocol) │
│  • IPC bridge               │                                │                             │
│                             │   Requests (Tauri→Python)      │  * reserved in v1, not fired│
│                             │     settings/get|set           │                             │
│                             │     mics/list|test             │  models/  (gitignored)      │
│                             │     history/list|clear|...     │                             │
│                             │     engine/set|reload          │                             │
│                             │     shutdown                   │                             │
└─────────────────────────────┘                                └─────────────────────────────┘
```

### 2.2 Responsibility split

**Tauri / Rust owns:**

- Overlay window (transparent, click-through, always-on-top, never-activating).
- Window lifecycle.
- Tray icon.
- Settings UI (React, rendered in a normal window).
- History UI (React, pure presentation layer — never duplicates state).
- Clipboard integration (read, backup, set, restore) — implemented in Rust to avoid per-platform keyboard-injection quirks.
- Auto-paste (simulate Ctrl+V via input simulation).
- System notifications.
- IPC bridge (WebSocket client + JSON-RPC dispatcher).

**Python owns:**

- Global hotkeys (pynput).
- Audio capture (sounddevice).
- Audio buffering (circular ring buffer).
- ASR model lifecycle.
- `BaseASR` interface and concrete engines (`NemotronASR`, `StubASR`).
- Streaming transcription hooks (reserved; not wired in v1).
- Settings persistence (`~/.nemotronflow/settings.json`).
- History persistence (SQLite at `~/.nemotronflow/history.db`).
- LLM cleanup pipeline (identity transform in v1).

**Communication contract:**

- JSON-RPC 2.0 over WebSocket.
- Single persistent connection.
- Single long-lived Python sidecar process.
- No per-utterance processes.
- Python keeps models warm.
- Fire-and-forget UI events.
- Overlay updates never block transcription.

### 2.3 Startup order (critical)

The user must never miss speech because initialization is still running. The order is:

1. Open microphone stream (pre-armed, capture-ready).
2. Register global hotkey (Ctrl + Space).
3. Start WebSocket server; emit `NEMOTRONFLOW_READY <port>` to stdout.
4. Load engine (`load()`).
5. Warmup (`warmup()`).
6. `health()` check — engine only becomes available after success.
7. Emit `model/status` = `ready`.
8. Tauri connects.

Because steps 1–3 complete before 4–7, an utterance captured during warmup is **buffered and transcribed as soon as the engine reports ready**. Audio is never lost.

### 2.4 Performance targets (the law)

| Path | Budget |
|---|---|
| Hotkey press → recording start | < 10 ms |
| Hotkey release → final transcript available | < 400 ms |
| Overlay updates | never block ASR |
| Clipboard restore | always off the critical path |

Release-to-paste latency budget breakdown (owned total ≤ 365 ms against the 400 ms target):

| Stage | Budget | Owner |
|---|---|---|
| 1. Hotkey release detected | < 2 ms | `hotkeys.py` (pynput thread) |
| 2. Capture stop + drain ring buffer | < 5 ms | `audio_capture.py` |
| 3. `EngineManager.transcribe()` dispatch | < 1 ms | `transcription.py` |
| 4. ASR inference (warm model) | ≤ 300 ms | `NemotronASR` (GPU strongly preferred) |
| 5. `llm_cleanup.clean()` (identity in v1) | < 1 ms | `llm_cleanup.py` |
| 6. `history.append()` | 0 ms on hot path | async worker (asyncio.Queue) |
| 7. WebSocket notify `transcription/result` | < 10 ms | `ipc/transport.py` |
| 8. Tauri receives + `clipboard.set` | < 10 ms | `clipboard.rs` |
| 9. `enigo` Ctrl+V simulation | < 5 ms | `autopaste.rs` |
| 10. Paste lands in target app | ~30–50 ms | OS / target app (not ours) |
| 11. Overlay → "✓ Done" | parallel | `overlay.rs` |

## 3. IPC protocol

### 3.1 Transport

- **JSON-RPC 2.0** over a single persistent **WebSocket** connection on localhost.
- Python is the WebSocket server; Tauri is the client.
- On launch, Python binds `127.0.0.1:0`, then prints exactly one line to stdout: `NEMOTRONFLOW_READY <port>`. Tauri parses that line and connects.
- Both directions supported on one connection (requests + notifications).
- Reconnect: on socket drop, Tauri retries every 500 ms for 10 s, then surfaces a "Backend disconnected" tray notification. Python re-emits `model/status` on reconnect so the UI resyncs.

### 3.2 Message catalogue

**Python → Tauri (notifications):**

| Method | Payload |
|---|---|
| `state/changed` | `{state: "idle"\|"listening"\|"processing"\|"done"\|"error", started_at?, utterance_id?}` |
| `transcription/result` | `{utterance_id, text, engine, audio_ms, total_ms, auto_paste, timestamp}` |
| `transcription/partial` | reserved (v1.x) |
| `vad/start`, `vad/end` | reserved (v2) |
| `model/status` | `{engine, phase: "loading"\|"ready"\|"error", detail?}` |
| `model/progress` | `{phase, percent, detail?}` |
| `download/progress` | `{model, percent, bytes, bytes_total}` |
| `log` | `{level, message}` |
| `error` | `{code, message, utterance_id?}` |

**Tauri → Python (requests, expect response):**

| Method | Input → Output |
|---|---|
| `settings/get` | → full settings object |
| `settings/set` | `{key, value}` → ack |
| `mics/list` | → `[{id, name, default?}]` |
| `mics/test` | `{mic_id}` → `{sample_rate, channels, peak_rms}` |
| `history/list` | `{limit, offset?, search?, engine?, favorite?}` → paginated |
| `history/add` | `{...}` → `{id}` |
| `history/delete` | `{id}` → ack |
| `history/edit` | `{id, text}` → ack |
| `history/favorite` | `{id, favorite}` → ack |
| `history/clear` | → ack |
| `history/export` | `{format: "json"\|"md"\|"csv"}` → `{path}` |
| `engine/set` | `{engine}` → ack |
| `engine/reload` | → ack |
| `shutdown` | → ack (graceful drain) |

## 4. Audio capture and ring buffer

### 4.1 Real-time-safety rules (hard invariants)

Inside the PortAudio callback, only these operations are permitted:

- Compute RMS.
- Write samples into the ring buffer.
- Return.

**Never:** allocate memory, block, perform RPC, acquire long-held locks, or call into NeMo.

### 4.2 Circular ring buffer

A **circular** ring buffer is used (constant memory, no large resets, zero allocations on the hot path, streaming-ready).

- Format contract: **float32, mono, 16 kHz.** `audio_capture.py` normalizes on the way in; every engine receives identical input.
- Capacity: `MAX_RECORDING_S + 1` seconds headroom.
- `start_recording()` (hot path on press): reset write pointer, set `recording = True`. Zero allocation.
- `stop_recording()` (hot path on release): return a **view** (`np.ndarray` slice) of the recorded range — no copy.
- Invariant: **only one utterance may exist at a time.** ASR must complete before the next recording starts. If future overlapping utterances are introduced, switch to copy-on-stop.

### 4.3 Recording cap

- Default hard cap: **30 s** (reduced from 60 s).
- On hitting the cap: automatically stop, transcribe what was captured, show "Maximum recording length reached." Prevents runaway memory.

### 4.4 Audio pipeline settings

| Setting | Default | Purpose |
|---|---|---|
| `sample_rate` | 16000 | NeMo contract |
| `audio_gain` | 1.0 | input amplification |
| `normalize_audio` | True | peak-normalize before ASR |
| `allow_resample` | True | allow engines with different SR requirements |
| `vad_enabled` | False | reserved (v2) |
| `vad_sensitivity` | "medium" | reserved (v2) |
| `silence_timeout_ms` | 800 | reserved (v2) |

### 4.5 Level meter (overlay)

- RMS computed per block in the callback.
- Emitted to the UI throttled to **30 Hz** (never at callback frequency).
- Drives the overlay waveform/level meter when `overlay_show_level_meter` is enabled.

## 5. ASR plugin layer

### 5.1 `BaseASR` contract

```python
class BaseASR(ABC):
    def name(self) -> str: ...
    def load(self) -> None: ...            # download weights if needed, load, warm. Idempotent.
    def unload(self) -> None: ...
    def is_ready(self) -> bool: ...
    def transcribe(self, audio: AudioChunk) -> TranscriptionResult: ...  # sync, blocking hot path
    def health(self) -> dict: ...          # diagnostics: device, precision, model name, memory
    def warmup(self) -> None: ...          # one dummy forward pass
    def device(self) -> str: ...           # "cuda" | "cpu"
    def supports_languages(self) -> list[str]: ...

    # v1.x streaming hooks — reserved, not called by v1 core
    def supports_streaming(self) -> bool: return False
    def stream(self, chunks: Iterator[AudioChunk]) -> Iterator[PartialResult]:
        raise NotImplementedError
    def info(self) -> dict: ...
```

Data types:

```python
@dataclass(frozen=True)
class AudioChunk:        # always float32 mono @ 16 kHz
    samples: np.ndarray
    sample_rate: int

@dataclass(frozen=True)
class Segment:
    text: str; start_ms: int; end_ms: int

@dataclass(frozen=True)
class TranscriptionResult:
    text: str; engine: str; audio_ms: int; inference_ms: int
    confidence: Optional[float] = None
    language: Optional[str] = None
    segments: tuple[Segment, ...] = ()

@dataclass(frozen=True)
class PartialResult:
    text: str; is_final: bool; sequence: int   # v1.x
```

### 5.2 Engines in v1

- **`NemotronASR`** — production engine. Loads a NeMo ASR checkpoint by configured path; checkpoint name defaults to `nemotron-asr-3.5` but is configurable, so any NeMo ASR `.nemo` file works. Weights downloaded lazily by `load()`. **The only production engine.**
- **`StubASR`** — dev only. Returns canned text after a realistic 60–120 ms sleep. Enables the full app loop without a model download. Only selectable when `dev_stub_engine` is true.

Future engines (not in v1): `WhisperASR`, `ParakeetASR`, `DeepgramASR`, `OpenAIRealtimeASR`, `GeminiLiveASR`.

### 5.3 `EngineManager` (singleton)

Exactly one engine loaded at a time. Owns the full model lifecycle. **No engine-specific code outside `EngineManager`.**

Responsibilities:

- `load` / `unload` / `reload` / `warmup` / `health`
- emit `model/status` events on every transition
- switching: `unload old → free memory → load new → warmup → health → emit ready`
- never keep multiple NeMo models warm simultaneously
- lazy-load on first use; keep model warm for the whole session

### 5.4 Warmup strategy

1. **Warm on startup, not on first use.** `EngineManager` loads + warms the configured engine before sending the READY line. The user's first Ctrl+Space hits a warm model.
2. **Warmup = one dummy forward pass** on a 1-second zero-filled `AudioChunk`. Forces graph allocation, kernel JIT, cache population.
3. **Warmup off the GUI thread.** During load:
   - Tauri shows "Initializing model…" tray state.
   - Capture stream is already armed; hotkey is already live.
   - **If the user presses Ctrl+Space during warmup, capture still records** — `transcribe()` is deferred until `is_ready()`. Audio is never lost; only the paste-back is delayed for that one utterance.
4. **Engine becomes available only after `health()` succeeds.** Warmup sequence is strictly: `load()` → `warmup()` → `health()` → emit `ready`.

### 5.5 Device selection

`nemotron_device = "auto"` → try CUDA → fall back to CPU. On CPU, still warm but expect ~1.5–3× inference time. The overlay and tray show a `CUDA` badge (muted green) or `CPU MODE` badge (muted amber) so latency surprises are explained.

### 5.6 Models folder

Gitignored. Configurable:

- `checkpoint_path`
- `device` (`auto` | `cuda` | `cpu`)
- `precision` (`fp16` | `fp32`)
- `batch_size`
- future model downloads (resumable; `download/progress` events)

## 6. Auto-paste and clipboard

Implemented entirely on the **Tauri / Rust** side (arboard for clipboard, enigo for input simulation). Python has no clipboard module.

### 6.1 Paste loop (on `transcription/result`)

```
1. backup = clipboard.read()
2. clipboard.set(text)
3. if auto_paste_enabled:
      if auto_paste_delay_ms: sleep(delay)
      enigo: key_down(Ctrl); key_click(V); key_up(Ctrl)
      sleep(40ms)   # let paste land
4. overlay → "✓ Pasted" (or "✓ Copied" if auto_paste off)
5. restore previous clipboard per restore mode
```

Focus is guaranteed to be on the user's target app because the overlay is click-through + never-activating (Windows `WS_EX_NOACTIVATE` + `WS_EX_TRANSPARENT`; macOS non-activating `NSWindow` with `ignoresMouseEvents`; Linux override-redirect / layer-shell with `KeyboardInteractivity::None`).

### 6.2 Clipboard restore modes (configurable)

`clipboard_restore_mode`:

- `immediate` — restore right after paste.
- `delayed` — **default.** Restore after `clipboard_restore_delay_ms` (default 2500 ms), asynchronously, off the critical path. Safer for clipboard managers and target-app race conditions.
- `disabled` — never restore; the transcript stays on the clipboard.

Clipboard restore is **always off the critical path**. The user perceives the paste immediately; the restore happens in the background.

### 6.3 Success / warning / error messages (overlay)

Kept extremely short.

- Success: `✓ Pasted`, `✓ Copied`, `✓ Saved`.
- Warnings: `⚠ CPU Mode`, `⚠ Initializing Model`.
- Errors: `✖ Backend Disconnected`, `✖ Model Error`, `⚠ Model loading failed`, `⚠ Microphone unavailable`.

## 7. Frontend (Tauri + React)

### 7.1 Three independent windows

| Window | Size | Transparent | Click-through | Always-on-top | Focus policy | When shown |
|---|---|---|---|---|---|---|
| `overlay` | 280–420 px wide × 56 px | Yes | **Yes** | Yes | **never activates** | on `state/changed` (hidden when idle) |
| `settings` | 720×560 px | No | No | No | normal focus | user opens |
| `history` | 760×600 px | No | No | No | normal focus | user opens |

Each window loads `index.html#/overlay`, `#/settings`, or `#/history` (hash routing inside one Vite build). The overlay must remain **invisible to focus** — never receive keyboard focus.

### 7.2 Overlay states & animation

Five states:

```
IDLE (hidden)
  ↓
LISTENING  ──▶ PROCESSING ──▶ DONE
                  ↓
                ERROR
```

- **Position snap points:** `top_center`, `top_right`, `bottom_center`, `bottom_right`, `custom` (future draggable positioning). Default `bottom_center`, `overlay_offset_y = 80`.
- **Dynamic width:** min 280 px, max 420 px. Long messages never wrap awkwardly.
- **Listening:** pulsing mic dot (1.4 s loop, opacity 0.45→1.0→0.45, ease-in-out). Optional live waveform driven by throttled RMS values. Optional timer (`0.0s → 4.2s`).
- **Processing:** mic dot → compact spinner, same bounding box (no layout shift).
- **Done:** "✓ Pasted" / "✓ Copied" depending on `auto_paste`.
- **Error:** `⚠` / `✖` message. Auto-hide after 1500 ms.
- **CPU/CUDA badge:** tiny pill in the corner, always visible while overlay is up.
- **Animation durations (consistent across states):**
  - Listening fade: 120 ms
  - Processing crossfade: 160 ms
  - Success hold: 600 ms
  - Error hold: 1500 ms
  - Hide: 180 ms
- **No Framer Motion.** CSS transitions + `requestAnimationFrame`. Target 60 FPS, < 16 ms render per frame.

### 7.3 Backend disconnection

- Show ERROR state.
- Auto-reconnect (500 ms × 20 attempts).
- Tray notification.
- **No modal dialogs.** Never interrupt the user.

### 7.4 Tray icon & menu

Tray icon color mirrors state: grey (idle/loading), pulsing red (listening), spinning (processing), green flash 400 ms (done).

Menu:

```
▶  Toggle listening        (Ctrl+Space)    — greyed if not ready
⚙  Settings…
🕘  History…
─────────────────
⚡  Engine: Nemotron ▸
      CUDA · Ready
─────────────────
⟳  Restart backend
♺  Reload engine
📋  Open logs
💾  Export diagnostics
─────────────────
⊟  Pause hotkey
Quit
```

### 7.5 React structure

```
frontend/src/
├── main.tsx                  # mounts <App/>; HashRouter → overlay|settings|history
├── App.tsx
├── overlay/
│   ├── Overlay.tsx           # state machine
│   ├── OverlayStates.tsx     # 5 visual states
│   ├── LevelMeter.tsx        # rAF-driven waveform (30 Hz)
│   └── overlay.css
├── settings/
│   ├── SettingsPage.tsx      # tabbed
│   ├── sections/
│   │   ├── GeneralTab.tsx    # hotkey, theme, tray, startup, overlay position
│   │   ├── AudioTab.tsx      # mic, gain, normalize, resample, VAD flags
│   │   ├── EngineTab.tsx     # engine select, checkpoint path, device override
│   │   ├── OutputTab.tsx     # auto-paste, delay, restore mode, restore delay
│   │   ├── HistoryTab.tsx    # enable, retention, export, clear
│   │   ├── AdvancedTab.tsx   # warmup_on_startup, engine_timeout_ms, precision,
│   │   │                      #   batch_size, future_download_models, device override
│   │   └── DevTab.tsx        # stub toggle, log level, diagnostic dump
│   └── controls/             # HotkeyPicker, MicDropdown, Toggle, Select, Slider
├── history/
│   ├── HistoryPage.tsx       # virtualized list, search, filters, pagination
│   ├── HistoryItem.tsx
│   ├── HistoryActions.tsx    # copy / edit / delete / favorite / export / tags
│   └── HistoryEmptyState.tsx
├── rpc/                      # client.ts, events.ts, types.ts (mirror of protocol.py)
├── hooks/                    # useBackend, useBackendEvent, useSettings, useHistory,
│                              #   useEngineStatus
├── theme/                    # tokens.css (dark-first), tokens.ts
└── assets/
```

History page is **virtualized** — never render thousands of items simultaneously. Supports search, copy, favorite, delete, export, tags. Semantic search is a future feature.

### 7.6 Theme

- **Dark mode is canonical.** All design tokens originate from dark mode. Light mode is secondary.
- **Font:** Inter, fallback `system-ui`. No custom font downloads.

Design tokens (excerpt):

```css
--bg-overlay:     rgba(22, 22, 26, 0.78);
--bg-elevated:    #1a1a1f;
--bg-surface:     #121216;
--accent:         #6366f1;
--success:        #22c55e;
--warning:        #f59e0b;   /* CPU MODE */
--danger:         #ef4444;   /* listening pulse, errors */
--radius-pill:    9999px;
--ease-out:       cubic-bezier(0.16, 1, 0.3, 1);
--dur-fast: 120ms; --dur-base: 180ms; --dur-slow: 320ms;
```

## 8. Settings schema

Persisted at `~/.nemotronflow/settings.json`. Atomic write (`tempfile` + `os.replace`), schema-validated on load (reject unknown keys, fall back to default for missing ones). `settings/set` triggers typed-merge + validate + save + emit `settings/changed`.

Every field has a default — the app boots usable on first run with **zero configuration**. If the Nemotron checkpoint path is `None` on first launch, the app loads `StubASR` automatically (with a tray "dev mode" badge) rather than crashing.

```python
@dataclass
class Settings:
    # --- Hotkey ---
    hotkey: str = "ctrl+space"
    hotkey_double_press_to_cancel: bool = False

    # --- Audio ---
    mic_device: Optional[int] = None
    sample_rate: int = 16000
    audio_gain: float = 1.0
    normalize_audio: bool = True
    allow_resample: bool = True
    noise_suppression: bool = False            # v1.x hook
    vad_enabled: bool = False                  # reserved (v2)
    vad_sensitivity: str = "medium"            # reserved (v2)
    silence_timeout_ms: int = 800              # reserved (v2)

    # --- Engine ---
    engine: str = "nemotron"                   # "nemotron" | "stub"
    nemotron_checkpoint_path: Optional[str] = None
    nemotron_device: str = "auto"              # "auto" | "cuda" | "cpu"
    nemotron_precision: str = "fp16"           # "fp16" | "fp32"
    nemotron_batch_size: int = 1
    nemotron_language: str = "en"
    engine_timeout_ms: int = 10000
    warmup_on_startup: bool = True
    keep_model_loaded: bool = True
    future_download_models: bool = False

    # --- Output ---
    auto_paste_enabled: bool = True
    auto_paste_delay_ms: int = 0
    clipboard_restore_mode: str = "delayed"    # "immediate" | "delayed" | "disabled"
    clipboard_restore_delay_ms: int = 2500

    # --- History ---
    history_enabled: bool = True
    history_retention_days: int = 90

    # --- LLM cleanup ---
    llm_cleanup_enabled: bool = False          # identity transform in v1
    llm_cleanup_provider: Optional[str] = None # openai|claude|gemini|ollama|lmstudio
    llm_cleanup_model: Optional[str] = None
    llm_cleanup_system_prompt: Optional[str] = None

    # --- UI ---
    theme: str = "dark"                        # "dark" | "light" | "system"
    overlay_position: str = "bottom_center"    # top_center|top_right|bottom_center|bottom_right|custom
    overlay_offset_x: int = 0
    overlay_offset_y: int = 80
    overlay_show_timer: bool = True
    overlay_show_level_meter: bool = False
    start_minimized_to_tray: bool = True
    run_on_startup: bool = False

    # --- Dev ---
    dev_stub_engine: bool = False
    log_level: str = "INFO"
```

## 9. History

**Source of truth: Python.** Tauri History UI is a pure presentation layer — never duplicates history state.

- Storage: SQLite at `~/.nemotronflow/history.db`.
- Table `transcriptions`:

| Column | Type |
|---|---|
| `id` | INTEGER PRIMARY KEY |
| `timestamp` | INTEGER (unix ms) |
| `text` | TEXT |
| `engine` | TEXT |
| `audio_ms` | INTEGER |
| `inference_ms` | INTEGER |
| `language` | TEXT |
| `favorite` | INTEGER (0/1) |
| `tags` | TEXT (JSON array) |

- Operations (owned by `history.py`): add, delete, edit, clear, export, favorite, search, filter, paginate.
- **Hot path: history insertion is async and never blocks transcription.** Fire into `asyncio.Queue`; a worker drains it.

## 10. LLM cleanup pipeline

File present in v1. Identity transform by default. Pipeline position:

```
audio → ASR → llm_cleanup → clipboard → paste
```

Prepared for: OpenAI, Claude, Gemini, Ollama, LM Studio, custom prompt cleanup. Disabled by default in v1.

## 11. Error handling & crash recovery

- **If the Python sidecar crashes:**
  - Restart automatically.
  - Reconnect WebSocket.
  - Restore overlay state.
  - Show `⚠ Backend restarted`.
  - No modal dialogs. No app restart required.

- **Warmup safety:** if Ctrl+Space is pressed during warmup, capture records; `transcribe()` is deferred until `is_ready()`. Recording is never rejected because the model is warming.

- **Engine load failure:** `model/status` = `error`; overlay shows `⚠ Model loading failed`; app continues to run on the Stub engine fallback (if enabled) or surfaces retry.

## 12. Logging & diagnostics

- **structlog**, JSONL lines to file, human-readable to stderr in dev.
- Sinks: `~/.nemotronflow/logs/nemotronflow.log`, rotating **5 MB × 5 files**.
- **`session_id` and `utterance_id` added to every log entry.**
- Levels: DEBUG < INFO < WARNING < ERROR < CRITICAL. Default INFO, dev DEBUG.
- No `print()` anywhere — enforced by ruff rule + CI grep.
- Every hot-path event logged with timing tags:

```
event=hotkey_press ts=...
event=capture_start ts=... delta_ms=2.1
event=transcribe_start utterance_id=... audio_ms=4200
event=transcribe_done utterance_id=... inference_ms=287 total_ms=312
event=paste_done utterance_id=... paste_ms=38 total_release_to_paste_ms=351
```

**Diagnostics export (user-initiated only, from tray → "Export diagnostics"):**

- Always user initiated.
- Bundles: last 5 MB of each log, sanitized settings, engine `health()` output, OS/GPU/mic info.
- **Redacts:** API keys, tokens, paths containing secrets.
- **Excludes `history.db` by default**; opt-in checkbox to include.
- Written to `~/Desktop/nemotronflow-diagnostics-<ts>.zip` after user confirmation.
- **Never uploaded automatically.**

## 13. Privacy

- **Local first.**
- No telemetry. No analytics. No crash reporting. No cloud dependencies.
- Everything explicit and opt-in.
- LLM cleanup, if enabled, is the only outbound call and requires an explicit provider + key.

## 14. Testing strategy

| Layer | Tool | What it proves |
|---|---|---|
| Unit (Python) | pytest | `BaseASR` contract conformance, ring buffer correctness + zero-copy invariant, settings validation, history CRUD, JSON-RPC framing, normalization math |
| Engine contract test | pytest | One `test_engine_contract(engine)` runs against `NemotronASR` (tiny fixture model) **and** `StubASR`. Both must pass identical assertions. |
| Unit (Rust) | `cargo test` | clipboard backup/restore, autopaste scheduling, RPC client framing, reconnect |
| Unit (TS) | vitest | RPC client, hooks, settings optimistic-update reducer, virtualization math |
| Component (TS) | vitest + Testing Library | overlay state machine transitions, settings form validation |
| Latency budget | pytest + `time.perf_counter_ns` | warm `transcribe()` ≤ 300 ms on StubASR (deterministic); Nemotron assertion `@pytest.mark.gpu`, skipped off GPU |
| Integration | pytest-asyncio | WS server + fake Tauri client drive a full utterance end-to-end (no real mic — `AudioCapture` injectable) |
| E2E smoke | Tauri + Playwright/tauri-driver | boot app, synthesized hotkey, assert overlay + clipboard. Nightly. |
| **Benchmark suite** | pytest-benchmark | hotkey latency, ASR latency, paste latency, startup time. Prevents regressions. |

`BaseASR` is mockable everywhere. Real NeMo only runs in the GPU-marked latency test.

## 15. CI/CD

Single GitHub Actions workflow, four jobs:

- **`lint-and-typecheck`** (always, < 60 s): ruff + black (Python), mypy `--strict` on `asr/`, `ipc/`, `server.py`, `cargo fmt` + `cargo clippy -D warnings`, `tsc --noEmit` + eslint.
- **`test`** (always, matrix ubuntu/macos/windows): `pytest -m "not gpu"`, `cargo test --release`, `vitest run`.
- **`build`** (on main + tags): `pnpm build`, `pyinstaller nemotronflow.spec` (→ `sidecar/<platform>/`), `cargo tauri build` (→ installers per OS).
- **`release`** (on tag `v*.*.*`): build all OSes, sign + notarize (macOS, secrets gated), upload to GitHub Release, write updater manifest `latest.json`.

**Nightly job** (not required for PR merge):

- E2E tests.
- GPU tests (self-hosted runner; `@pytest.mark.gpu`).
- Packaging tests (build installers for every OS).

- Branch protection: `main` requires green CI + 1 review.
- PR template + issue templates (bug / feature / engine-plugin).
- CODEOWNERS for backend / frontend / packaging.
- `dependabot.yml` (npm + cargo + pip + Actions).
- Secrets hygiene: no keys in repo; `.env.example` documents local-dev needs.

## 16. Release channels

- `stable`, `beta`, `nightly`.
- Updater channel configurable in settings.

## 17. Packaging

### 17.1 Strategy

- **Light installer (default, ~80 MB):** CPU mode, lazy model download.
- **Optional GPU bundle (~1.5 GB):** CUDA libraries included.

This avoids forcing CUDA on every user.

### 17.2 PyInstaller (always `--onedir`, never `--onefile`)

Fast startup is more important than a single executable. Hidden imports for `nemo_*`, `sounddevice`, `pynput._util.win32` enumerated in `nemotronflow.spec`.

### 17.3 Tauri wraps the sidecar

`tauri.conf.json` declares the sidecar via `bundle.externalBin`. `tauri-plugin-shell` provides a stable resolvable path. The Tauri bundler produces the final installer: `.msi`/`.exe` (Windows NSIS), `.dmg` (macOS), `.deb`/`.AppImage` (Linux). One installer, one icon, one Start Menu entry.

### 17.4 Platform specifics

- **Windows:** cleanest target. WebView2 preinstalled on Win11. NSIS installer with auto-update via `tauri-plugin-updater`.
- **macOS:** code signing + notarization required (gate behind signing identity in CI). Prompts for Accessibility + Input Monitoring permissions on first run; docs explain the one-time grant.
- **Linux:** X11 works cleanly today; Wayland layer-shell works on wlroots-based compositors (GNOME/KDE Wayland need `xdg-desktop-portal`). Documented, not hidden.

### 17.5 Resumable model downloads

Interrupted model downloads continue instead of restarting. `download/progress` events already exist in the protocol.

### 17.6 Installer behavior

- First run: boots to tray, captures immediately via StubASR (no model yet), tray tooltip "NemotronFlow ready — press Ctrl+Space." One-screen "Get started" card in Settings: grant permissions (macOS), pick mic, optionally download Nemotron model.
- Updates: `tauri-plugin-updater` checks a GitHub Releases JSON endpoint silently on startup and on-demand from tray. Signed update; small toast (no modal).
- Uninstall: clean — everything under one app-data dir + one Program Files dir.

## 18. Repository structure

```
NemotronFlow/
├── .github/
│   ├── workflows/ci.yml
│   ├── ISSUE_TEMPLATE/{bug_report,feature_request,engine_plugin}.md
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── CODEOWNERS
├── .gitignore, .gitattributes
├── LICENSE                          # MIT
├── README.md
├── ROADMAP.md                       # V1 / V1.1 / V2 / future engines / streaming / voice
│                                    #   commands / LLM cleanup / semantic history
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
├── CHANGELOG.md                     # keepachangelog format
├── pyproject.toml
├── requirements.txt, requirements-dev.txt
├── package.json
├── pnpm-workspace.yaml
├── Makefile                         # make dev / test / build / dist
├── docs/
│   ├── architecture.md
│   ├── getting-started.md
│   ├── engines.md
│   ├── troubleshooting.md
│   └── superpowers/specs/2026-06-19-nemotronflow-design.md   ← this file
├── backend/
│   └── nemotronflow/
│       ├── __init__.py
│       ├── __main__.py
│       ├── server.py                # WebSocket JSON-RPC server, lifecycle
│       ├── hotkeys.py               # pynput global Ctrl+Space
│       ├── audio_capture.py         # sounddevice + circular ring buffer
│       ├── transcription.py         # orchestrator: capture-end → engine → result
│       ├── history.py               # SQLite add/delete/edit/clear/export
│       ├── settings.py              # schema, load/save, defaults, change events
│       ├── llm_cleanup.py           # identity transform in v1
│       ├── logging.py               # structlog setup
│       ├── asr/
│       │   ├── __init__.py
│       │   ├── base.py              # BaseASR, AudioChunk, TranscriptionResult, PartialResult
│       │   ├── nemotron.py          # NemotronASR (NeMo)
│       │   ├── stub.py              # StubASR (dev only)
│       │   └── factory.py           # ENGINE_REGISTRY + EngineManager
│       ├── ipc/
│       │   ├── __init__.py
│       │   ├── protocol.py          # JSON-RPC 2.0 envelope, method catalogue
│       │   └── transport.py         # WebSocket framing, reconnect, backpressure
│       ├── config/
│       │   └── default_settings.json
│       └── models/                  # gitignored
│           └── .gitkeep
└── frontend/
    ├── package.json, tsconfig.json, vite.config.ts, index.html
    ├── src/                         # (Section 7.5 layout)
    └── src-tauri/
        ├── Cargo.toml, tauri.conf.json, build.rs
        └── src/
            ├── main.rs
            ├── sidecar.rs           # spawn Python, read READY line, port handoff
            ├── rpc.rs               # WS client + JSON-RPC dispatcher
            ├── clipboard.rs         # arboard wrapper: read/backup/set/restore
            ├── autopaste.rs         # enigo Ctrl+V + restore scheduling
            ├── tray.rs
            ├── overlay.rs           # click-through + WS_EX_NOACTIVATE + always-on-top
            └── logging.rs
```

## 19. MVP milestones

- **M1 — Backend sidecar + StubASR.** Circular ring buffer, hotkey (pynput), WebSocket server, JSON-RPC protocol, StubASR end-to-end transcription.
- **M2 — Overlay + IPC.** Three-window Tauri shell, overlay state machine, click-through + never-activate, tray, Tauri↔Python WebSocket bridge.
- **M3 — Clipboard + auto-paste.** arboard backup/set/restore, enigo Ctrl+V, restore modes (`immediate`/`delayed`/`disabled`).
- **M4 — History + settings.** SQLite history, virtualized History UI, settings persistence, Settings tabs (incl. Advanced + Dev).
- **M5 — NemotronASR integration.** NeMo-backed `NemotronASR`, `EngineManager` lifecycle, warmup, health, device selection, model download with resumable `download/progress`.
- **M6 — Packaging + installer.** PyInstaller `--onedir`, Tauri sidecar bundling, NSIS/DMG/deb/AppImage, auto-update.
- **M7 — Alpha release.** README with animated GIF, ROADMAP, release channels, signed artifacts, GitHub Release.

## 20. Glossary

- **Sidecar** — the long-lived Python process spawned and supervised by Tauri.
- **BaseASR** — the abstract ASR interface every engine implements.
- **EngineManager** — the singleton owning the single live engine and its lifecycle.
- **Ring buffer** — the circular pre-allocated audio buffer; zero-allocation on the hot path.
- **READY line** — the single `NEMOTRONFLOW_READY <port>` line Python prints to stdout once its WebSocket server is bound.
- **Warmup** — one dummy forward pass at startup so the user's first utterance hits a primed model.
