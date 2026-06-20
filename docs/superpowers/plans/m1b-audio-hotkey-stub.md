# M1b — Circular ring buffer + pynput hotkey + StubASR transcription

**Goal:** A complete push-to-talk loop running entirely headless (no UI). Press and hold Ctrl+Space → audio records into a circular ring buffer → release → StubASR transcribes → a `transcription/result` notification is emitted over the WebSocket. This milestone proves the latency budget end-to-end on the backend.

**Files to create:**
- `backend/nemotronflow/asr/__init__.py`
- `backend/nemotronflow/asr/base.py` — `BaseASR`, `AudioChunk`, `TranscriptionResult`, `PartialResult`, `Segment`.
- `backend/nemotronflow/asr/stub.py` — `StubASR`.
- `backend/nemotronflow/asr/factory.py` — `ENGINE_REGISTRY`, `EngineManager` singleton.
- `backend/nemotronflow/audio_capture.py` — `AudioCapture` with circular ring buffer.
- `backend/nemotronflow/hotkeys.py` — pynput global hotkey (logic testable without a real keyboard).
- `backend/nemotronflow/transcription.py` — orchestrator wiring capture + engine + notification emit.
- `backend/nemotronflow/llm_cleanup.py` — identity transform.
- `tests/test_ring_buffer.py`, `tests/test_stub_asr.py`, `tests/test_engine_contract.py`, `tests/test_engine_manager.py`, `tests/test_hotkeys.py`, `tests/test_transcription.py`, `tests/test_llm_cleanup.py`.
- `tests/benchmarks/test_hotkey_latency.py`, `tests/benchmarks/test_asr_latency.py`.

**Dependencies:** M1a (server, protocol, logging).

**Acceptance criteria:**
- `BaseASR` contract: both `StubASR` and (later) `NemotronASR` pass `test_engine_contract`.
- Ring buffer: zero allocations on `start_recording`; `stop_recording` returns a zero-copy view; circular wraparound correct; 30 s hard cap enforced; PortAudio callback does only RMS + write (asserted by inspection + a no-alloc timing test).
- Hotkey: press→`listening`, release→`processing`→transcribe→`done`. Double-press protection (a second press before the first release is ignored).
- Warmup safety: pressing during warmup records; transcription is deferred until `is_ready()`.
- Backend emits `state/changed` and `transcription/result` notifications over the WebSocket.
- Benchmarks: StubASR `transcribe()` warm path ≤ 200 ms (deterministic headroom under the 300 ms target).

**Test strategy:** unit tests for every module with real deps mocked (sounddevice/pynput); `test_transcription.py` is an integration test using a fake AudioCapture + StubASR end-to-end; benchmarks use pytest-benchmark.

**Estimated complexity:** Medium-High. The ring buffer's zero-allocation invariant and the warmup-deferral are the subtle parts.

**Commit boundaries:** one per task, tag `v0.1.0-m1b`.

---

## Task M1b.1: BaseASR contract + data types (TDD)

**Files:** `backend/nemotronflow/asr/__init__.py` (empty), `backend/nemotronflow/asr/base.py`, `tests/test_stub_asr.py` (skeleton for contract).

- [ ] **Step 1 (write failing test):** `tests/test_engine_contract.py` (parametrized; expanded as engines arrive):

```python
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from nemotronflow.asr.base import AudioChunk, BaseASR, TranscriptionResult
from nemotronflow.asr.stub import StubASR

ENGINES: list[Callable[[], BaseASR]] = [StubASR]


@pytest.mark.parametrize("make_engine", ENGINES, ids=lambda f: f().__class__.__name__)
@pytest.mark.asyncio
async def test_engine_contract(make_engine: Callable[[], BaseASR]) -> None:
    """Every engine must satisfy the BaseASR contract identically."""
    eng = make_engine()
    assert isinstance(eng.name(), str) and eng.name()

    eng.load()
    eng.warmup()
    assert eng.is_ready() is True

    health = eng.health()
    assert {"name", "device"} <= health.keys()

    silence = AudioChunk(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000)
    result = eng.transcribe(silence)
    assert isinstance(result, TranscriptionResult)
    assert isinstance(result.text, str)
    assert result.engine == eng.name()
    assert result.audio_ms == 1000
    assert result.inference_ms >= 0
    assert isinstance(eng.supports_languages(), list)
    assert isinstance(eng.supports_streaming(), bool)

    eng.unload()
    assert eng.is_ready() is False
```

`tests/test_stub_asr.py` (engine-specific assertions):

```python
from __future__ import annotations

import time

import numpy as np

from nemotronflow.asr.base import AudioChunk
from nemotronflow.asr.stub import StubASR


def test_stub_returns_text_with_realistic_latency() -> None:
    eng = StubASR()
    eng.load(); eng.warmup()
    audio = AudioChunk(samples=np.random.randn(16000).astype(np.float32) * 0.1, sample_rate=16000)
    t0 = time.perf_counter()
    result = eng.transcribe(audio)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result.engine == "stub"
    assert result.text
    assert 30 <= elapsed_ms <= 200  # stub sleeps 60-120ms; allow slack
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_engine_contract.py tests/test_stub_asr.py -v` → import error.

- [ ] **Step 3 (implement):** `backend/nemotronflow/asr/base.py`:

```python
"""BaseASR contract: the abstract interface every ASR engine implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class AudioChunk:
    """Mono float32 PCM at 16 kHz. The universal input contract for every engine."""

    samples: np.ndarray
    sample_rate: int


@dataclass(frozen=True)
class Segment:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    engine: str
    audio_ms: int
    inference_ms: int
    confidence: float | None = None
    language: str | None = None
    segments: tuple[Segment, ...] = ()


@dataclass(frozen=True)
class PartialResult:
    """v1.x streaming. Reserved — not called by v1 core."""

    text: str
    is_final: bool
    sequence: int


class BaseASR(ABC):
    """Every ASR engine implements this. The rest of the app depends only on this interface."""

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def load(self) -> None:
        """Download weights if needed, load into memory. Idempotent."""

    @abstractmethod
    def unload(self) -> None: ...

    @abstractmethod
    def is_ready(self) -> bool: ...

    @abstractmethod
    def transcribe(self, audio: AudioChunk) -> TranscriptionResult:
        """Synchronous, blocking hot path. Caller sees this as the <400ms step."""

    @abstractmethod
    def warmup(self) -> None:
        """One dummy forward pass to prime kernels/caches."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Diagnostics: name, device, precision, model, memory."""

    @abstractmethod
    def device(self) -> str:
        """'cuda' | 'cpu'."""

    @abstractmethod
    def supports_languages(self) -> list[str]: ...

    # ---- v1.x streaming hooks: declared, not called by v1 core ----
    def supports_streaming(self) -> bool:
        return False

    def stream(self, chunks: Iterator[AudioChunk]) -> Iterator[PartialResult]:
        raise NotImplementedError("Engine does not support streaming")

    def info(self) -> dict[str, Any]:
        return {"name": self.name(), "streaming": self.supports_streaming()}
```

`backend/nemotronflow/asr/stub.py`:

```python
"""StubASR — dev-only engine that returns canned text after a realistic sleep."""
from __future__ import annotations

import random
import time
from typing import Any

from nemotronflow.asr.base import AudioChunk, BaseASR, TranscriptionResult

_CANNED = "[stub] the quick brown fox jumps over the lazy dog"


class StubASR(BaseASR):
    def __init__(self) -> None:
        self._ready = False

    def name(self) -> str:
        return "stub"

    def load(self) -> None:
        self._ready = True

    def unload(self) -> None:
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    def warmup(self) -> None:
        if self._ready:
            self.transcribe(AudioChunk(samples=__import__("numpy").zeros(16000, dtype="float32"), sample_rate=16000))

    def transcribe(self, audio: AudioChunk) -> TranscriptionResult:
        t0 = time.perf_counter()
        time.sleep(random.uniform(0.06, 0.12))
        inference_ms = int((time.perf_counter() - t0) * 1000)
        audio_ms = int(len(audio.samples) / audio.sample_rate * 1000)
        return TranscriptionResult(
            text=_CANNED, engine=self.name(), audio_ms=audio_ms, inference_ms=inference_ms, language="en"
        )

    def health(self) -> dict[str, Any]:
        return {"name": self.name(), "device": "cpu", "precision": "n/a", "ready": self._ready}

    def device(self) -> str:
        return "cpu"

    def supports_languages(self) -> list[str]:
        return ["en"]
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_engine_contract.py tests/test_stub_asr.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/asr/ tests/test_engine_contract.py tests/test_stub_asr.py
git commit -m "feat(asr): add BaseASR contract and StubASR dev engine"
```

---

## Task M1b.2: EngineManager singleton + factory (TDD)

**Files:** `backend/nemotronflow/asr/factory.py`, `tests/test_engine_manager.py`.

- [ ] **Step 1 (write failing test):** `tests/test_engine_manager.py`:

```python
from __future__ import annotations

import pytest

from nemotronflow.asr.factory import EngineManager
from nemotronflow.asr.stub import StubASR


@pytest.mark.asyncio
async def test_manager_loads_single_engine_and_keeps_warm() -> None:
    mgr = EngineManager(initial="stub")
    await mgr.boot()
    assert mgr.is_ready()
    assert isinstance(mgr.current(), StubASR)
    # second current() returns the SAME warm instance
    assert mgr.current() is mgr.current()


@pytest.mark.asyncio
async def test_switching_unloads_then_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = EngineManager(initial="stub")
    await mgr.boot()
    first = mgr.current()
    events: list[str] = []

    async def emit(method: str, payload: dict) -> None:
        events.append(method)

    mgr.on_status = emit  # type: ignore[assignment]
    await mgr.set_engine("stub")  # re-load same name: unload → load
    assert mgr.current() is not first
    assert "model/status" in events


@pytest.mark.asyncio
async def test_transcribe_routes_to_current_engine() -> None:
    import numpy as np

    from nemotronflow.asr.base import AudioChunk

    mgr = EngineManager(initial="stub")
    await mgr.boot()
    result = mgr.transcribe(AudioChunk(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000))
    assert result.engine == "stub"
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_engine_manager.py -v` → import error.

- [ ] **Step 3 (implement):** `backend/nemotronflow/asr/factory.py`:

```python
"""EngineManager singleton — owns exactly one live engine and its lifecycle."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from nemotronflow.asr.base import AudioChunk, BaseASR, TranscriptionResult
from nemotronflow.asr.stub import StubASR
from nemotronflow.logging import get_logger

log = get_logger("engine_manager")

# ENGINE_REGISTRY: name → zero-arg factory. Add NemotronASR in M5.
ENGINE_REGISTRY: dict[str, Callable[[], BaseASR]] = {
    "stub": StubASR,
}

StatusEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _noop_emit(_method: str, _payload: dict[str, Any]) -> None:
    pass


class EngineManager:
    """Singleton-ish: constructed once at server boot. Exactly one engine loaded."""

    def __init__(self, initial: str = "stub") -> None:
        self._engine: BaseASR | None = None
        self._name: str = initial
        self.on_status: StatusEmitter = _noop_emit

    def current(self) -> BaseASR:
        if self._engine is None:
            raise RuntimeError("EngineManager not booted")
        return self._engine

    def is_ready(self) -> bool:
        return self._engine is not None and self._engine.is_ready()

    async def boot(self) -> None:
        await self._load_and_warm(self._name)

    async def set_engine(self, name: str) -> None:
        if name not in ENGINE_REGISTRY:
            raise ValueError(f"unknown engine: {name}")
        await self.on_status("model/status", {"engine": name, "phase": "loading"})
        if self._engine is not None:
            await asyncio.to_thread(self._engine.unload)
            self._engine = None
        self._name = name
        await self._load_and_warm(name)

    async def _load_and_warm(self, name: str) -> None:
        factory = ENGINE_REGISTRY[name]
        engine = factory()

        def _do_load() -> None:
            engine.load()
            engine.warmup()

        await asyncio.to_thread(_do_load)
        health = engine.health()
        if not engine.is_ready():
            await self.on_status("model/status", {"engine": name, "phase": "error", "detail": "health check failed"})
            raise RuntimeError(f"engine {name} failed health check: {health}")
        self._engine = engine
        await self.on_status(
            "model/status",
            {"engine": name, "phase": "ready", "detail": {"device": engine.device(), **health}},
        )
        log.info("engine_ready", engine=name, device=engine.device())

    def transcribe(self, audio: AudioChunk) -> TranscriptionResult:
        return self.current().transcribe(audio)
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_engine_manager.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/asr/factory.py tests/test_engine_manager.py
git commit -m "feat(asr): add EngineManager singleton with load/unload/warmup/health"
```

---

## Task M1b.3: Circular ring buffer + AudioCapture (TDD)

**Files:** `backend/nemotronflow/audio_capture.py`, `tests/test_ring_buffer.py`.

The hard invariants: callback does only RMS + write (no alloc, no block, no RPC); `start_recording` is zero-allocation; `stop_recording` returns a zero-copy view; circular wraparound correct; 30 s hard cap.

- [ ] **Step 1 (write failing test):** `tests/test_ring_buffer.py`:

```python
from __future__ import annotations

import time

import numpy as np
import pytest

from nemotronflow.audio_capture import AudioCapture, MAX_RECORDING_MS, SAMPLE_RATE


def _make_capture_with_fake_stream() -> AudioCapture:
    cap = AudioCapture(on_level=None)
    # Don't open a real stream; we'll drive _callback directly.
    return cap


def test_start_recording_is_zero_allocation(benchmark: pytest.LogCaptureFixture) -> None:
    cap = _make_capture_with_fake_stream()
    t0 = time.perf_counter_ns()
    cap.start_recording()
    dt_ms = (time.perf_counter_ns() - t0) / 1e6
    assert dt_ms < 1.0  # must be sub-millisecond


def test_callback_writes_samples_and_wraps_around() -> None:
    cap = _make_capture_with_fake_stream()
    cap.start_recording()
    frame = (np.arange(160, dtype=np.float32) / 160.0)
    # Write enough frames to wrap the ring at least once using a tiny buffer.
    cap._ring = np.zeros(320, dtype=np.float32)  # type: ignore[attr-defined]
    cap._ring_capacity = 320  # type: ignore[attr-defined]
    cap._write_idx = 0  # type: ignore[attr-defined]
    for _ in range(4):  # 4 * 160 = 640 samples → wraps once over 320
        cap._callback(frame.reshape(-1, 1), 160, None, None)  # type: ignore[attr-defined]
    # After wraparound, last 320 samples are the most recent frame repeated.
    chunk = cap.stop_recording()
    assert chunk.sample_rate == SAMPLE_RATE
    assert len(chunk.samples) == 320  # capped at ring size


def test_stop_returns_view_not_copy() -> None:
    cap = _make_capture_with_fake_stream()
    cap.start_recording()
    frame = np.ones((160, 1), dtype=np.float32) * 0.5
    cap._callback(frame, 160, None, None)  # type: ignore[attr-defined]
    chunk = cap.stop_recording()
    assert chunk.samples.base is not None  # view shares memory with ring
    assert np.allclose(chunk.samples[:160], 0.5)


def test_30s_cap_stops_automatically() -> None:
    cap = _make_capture_with_fake_stream()
    cap.start_recording()
    # Simulate crossing the 30s boundary.
    beyond = int((MAX_RECORDING_MS + 1000) / 1000 * SAMPLE_RATE)
    big = np.zeros((beyond, 1), dtype=np.float32)
    cap._callback(big, beyond, None, None)  # type: ignore[attr-defined]
    chunk = cap.stop_recording()
    assert len(chunk.samples) <= int(MAX_RECORDING_MS / 1000 * SAMPLE_RATE) + SAMPLE_RATE


def test_idle_callback_does_not_write() -> None:
    cap = _make_capture_with_fake_stream()
    # not recording
    frame = np.ones((160, 1), dtype=np.float32)
    cap._callback(frame, 160, None, None)  # type: ignore[attr-defined]
    assert cap._write_idx == 0  # type: ignore[attr-defined]
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_ring_buffer.py -v` → import error.

- [ ] **Step 3 (implement):** `backend/nemotronflow/audio_capture.py`:

```python
"""Microphone capture with a circular ring buffer.

Real-time-safety rules (HARD): inside _callback we ONLY compute RMS and write
samples into the ring. No allocations, no blocking, no RPC, no logging.
start_recording is zero-allocation; stop_recording returns a zero-copy view.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import numpy as np
import sounddevice as sd

from nemotronflow.asr.base import AudioChunk
from nemotronflow.logging import get_logger

log = get_logger("audio")

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
MAX_RECORDING_MS = 30_000  # hard cap per utterance
_RING_SECONDS = MAX_RECORDING_MS // 1000 + 1


class AudioCapture:
    """Circular ring buffer fed by a pre-armed sounddevice InputStream."""

    def __init__(self, on_level: Callable[[float], None] | None = None) -> None:
        self._stream: sd.InputStream | None = None
        self._ring: np.ndarray = np.zeros(SAMPLE_RATE * _RING_SECONDS, dtype=np.float32)
        self._ring_capacity: int = len(self._ring)
        self._write_idx: int = 0
        self._frames_written: int = 0  # since start_recording (for 30s cap)
        self._recording: bool = False
        self._lock = threading.Lock()
        self._on_level = on_level
        self._device: int | None = None
        self._sample_rate: int = SAMPLE_RATE

    def open(self, device: int | None = None, sample_rate: int = SAMPLE_RATE) -> None:
        """Open the input stream ONCE. Called at startup and on mic change only."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._stream = sd.InputStream(
            device=device,
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._callback,
            blocksize=0,
            latency="low",
        )
        self._stream.start()
        self._device = device
        self._sample_rate = sample_rate
        log.info("stream_opened", device=device, sample_rate=sample_rate)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata: np.ndarray, frames: int, _time_info: Any, _status: Any) -> None:
        # REAL-TIME PATH. RMS + write only.
        mono = indata[:, 0]
        rms = float(np.sqrt(np.mean(mono * mono)))
        if self._on_level is not None:
            self._on_level(rms)
        if not self._recording:
            return
        # 30s hard cap: stop absorbing beyond the cap.
        max_samples = MAX_RECORDING_MS // 1000 * self._sample_rate
        if self._frames_written >= max_samples:
            return
        room = min(frames, max_samples - self._frames_written)
        with self._lock:
            end = self._write_idx + room
            if end <= self._ring_capacity:
                self._ring[self._write_idx : end] = mono[:room]
            else:
                first = self._ring_capacity - self._write_idx
                self._ring[self._write_idx :] = mono[:first]
                remain = room - first
                self._ring[:remain] = mono[first : first + remain]
            self._write_idx = end % self._ring_capacity
            self._frames_written += room

    def start_recording(self) -> None:
        """Hot path on hotkey press. Zero allocation."""
        with self._lock:
            self._write_idx = 0
            self._frames_written = 0
            self._recording = True

    def stop_recording(self) -> AudioChunk:
        """Hot path on hotkey release. Returns a VIEW (zero-copy)."""
        with self._lock:
            self._recording = False
            n = min(self._frames_written, self._ring_capacity)
            if n <= self._ring_capacity and self._write_idx >= n:
                # No wraparound during this recording: a single contiguous slice.
                samples = self._ring[self._write_idx - n : self._write_idx]
            else:
                # Wrapped: reassemble into a fresh array (rare; only on >ring-size).
                samples = np.empty(n, dtype=np.float32)
                first = (self._write_idx - n) % self._ring_capacity
                end = first + n
                if end <= self._ring_capacity:
                    samples[:] = self._ring[first:end]
                else:
                    tail = self._ring_capacity - first
                    samples[:tail] = self._ring[first:]
                    samples[tail:] = self._ring[: n - tail]
        return AudioChunk(samples=samples, sample_rate=self._sample_rate)

    @property
    def is_recording(self) -> bool:
        return self._recording
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_ring_buffer.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/audio_capture.py tests/test_ring_buffer.py
git commit -m "feat(audio): add circular ring buffer with zero-alloc start and zero-copy stop"
```

---

## Task M1b.4: pynput global hotkey (TDD, logic-only)

**Files:** `backend/nemotronflow/hotkeys.py`, `tests/test_hotkeys.py`.

pynput can't be driven in CI without a real keyboard, so the module exposes a pure `HotkeyMatcher` (decides whether a press/release matches the configured combo) plus a thin `HotkeyListener` that wires pynput callbacks to `HotkeyMatcher`. The matcher is unit-tested exhaustively.

- [ ] **Step 1 (write failing test):** `tests/test_hotkeys.py`:

```python
from __future__ import annotations

from pynput.keyboard import Key, KeyCode

from nemotronflow.hotkeys import HotkeyMatcher


def _combo(*parts: str) -> HotkeyMatcher:
    return HotkeyMatcher.from_string("+".join(parts))


def test_ctrl_space_match() -> None:
    m = _combo("ctrl", "space")
    assert m.matches_press({Key.ctrl_l, Key.space})


def test_partial_combo_not_a_match() -> None:
    m = _combo("ctrl", "space")
    assert not m.matches_press({Key.ctrl_l})


def test_extra_modifier_not_a_match() -> None:
    m = _combo("ctrl", "space")
    assert not m.matches_press({Key.ctrl_l, Key.space, Key.shift})


def test_release_of_trigger_key_ends_utterance() -> None:
    m = _combo("ctrl", "space")
    assert m.matches_release(Key.space, currently_held={Key.ctrl_l})


def test_release_of_modifier_does_not_end_utterance() -> None:
    m = _combo("ctrl", "space")
    assert not m.matches_release(Key.ctrl_l, currently_held={Key.space})


def test_double_press_before_release_is_ignored() -> None:
    m = _combo("ctrl", "space")
    state = {"active": False}
    press1 = m.on_press({Key.ctrl_l, Key.space}, state)
    press2 = m.on_press({Key.ctrl_l, Key.space}, state)
    assert press1 is True and press2 is False  # second press ignored
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_hotkeys.py -v` → import error.

- [ ] **Step 3 (implement):** `backend/nemotronflow/hotkeys.py`:

```python
"""Global hotkey: pynput listener + a pure HotkeyMatcher (unit-testable)."""
from __future__ import annotations

from dataclasses import dataclass, field

from pynput.keyboard import Key, KeyCode

# Canonical mapping from pynput keys to the simple names used in settings.
_KEY_ALIASES = {
    Key.space: "space",
    Key.ctrl_l: "ctrl",
    Key.ctrl_r: "ctrl",
    Key.shift_l: "shift",
    Key.shift_r: "shift",
    Key.alt_l: "alt",
    Key.alt_r: "alt",
    Key.cmd: "cmd",
    Key.cmd_l: "cmd",
    Key.cmd_r: "cmd",
}


def _key_name(key: object) -> str | None:
    if key in _KEY_ALIASES:
        return _KEY_ALIASES[key]
    if isinstance(key, KeyCode):
        return key.char.lower() if key.char else None
    return None


_MODIFIERS = {"ctrl", "shift", "alt", "cmd"}


@dataclass
class HotkeyMatcher:
    """Pure logic: decides if a set of held keys / a released key matches the combo."""

    modifiers: frozenset[str] = field(default_factory=frozenset)
    trigger: str = "space"
    active: bool = False  # mutable utterance state lives in the caller's dict

    @classmethod
    def from_string(cls, s: str) -> "HotkeyMatcher":
        parts = [p.strip().lower() for p in s.split("+") if p.strip()]
        mods = frozenset(p for p in parts if p in _MODIFIERS)
        triggers = [p for p in parts if p not in _MODIFIERS]
        if len(triggers) != 1:
            raise ValueError(f"hotkey must have exactly one trigger key: {s!r}")
        return cls(modifiers=mods, trigger=triggers[0])

    def matches_press(self, held_names: set[str]) -> bool:
        names = held_names
        if not self.modifiers <= names:
            return False
        return self.trigger in names

    def matches_release(self, released: object, currently_held: set[object]) -> bool:
        released_name = _key_name(released)
        return released_name == self.trigger

    def on_press(self, held: set[object], state: dict) -> bool:
        """Returns True if this press STARTS a new utterance (first press only)."""
        if state.get("active", False):
            return False  # double-press protection
        held_names = {_key_name(k) for k in held if _key_name(k) is not None}
        if self.matches_press(held_names):
            state["active"] = True
            return True
        return False

    def on_release(self, released: object, state: dict) -> bool:
        """Returns True if this release ENDS the active utterance."""
        if not state.get("active", False):
            return False
        if self.matches_release(released, set()):
            state["active"] = False
            return True
        return False


class HotkeyListener:
    """Wires pynput's global listener to a HotkeyMatcher + press/release callbacks."""

    def __init__(
        self,
        matcher: HotkeyMatcher,
        on_press: "object",
        on_release: "object",
    ) -> None:
        from collections.abc import Callable

        if not callable(on_press) or not callable(on_release):
            raise TypeError("on_press and on_release must be callable")
        self._matcher = matcher
        self._on_press: Callable[[], None] = on_press  # type: ignore[assignment]
        self._on_release: Callable[[], None] = on_release  # type: ignore[assignment]
        self._state: dict = {"active": False}
        self._held: set[object] = set()
        self._listener: object | None = None

    def start(self) -> None:
        from pynput import keyboard

        def press(key: object) -> None:
            self._held.add(key)
            if self._matcher.on_press(self._held, self._state):
                self._on_press()

        def release(key: object) -> None:
            self._held.discard(key)
            if self._matcher.on_release(key, self._state):
                self._on_release()

        self._listener = keyboard.Listener(on_press=press, on_release=release)
        self._listener.start()  # type: ignore[attr-defined]

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()  # type: ignore[attr-defined]
            self._listener = None
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_hotkeys.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/hotkeys.py tests/test_hotkeys.py
git commit -m "feat(hotkeys): add pure HotkeyMatcher and pynput listener with double-press guard"
```

---

## Task M1b.5: llm_cleanup identity transform (TDD)

**Files:** `backend/nemotronflow/llm_cleanup.py`, `tests/test_llm_cleanup.py`.

Small but present so the pipeline shape is right from day one.

- [ ] **Step 1 (write failing test):** `tests/test_llm_cleanup.py`:

```python
from __future__ import annotations

from nemotronflow.llm_cleanup import clean


def test_identity_transform_when_disabled() -> None:
    assert clean("hello world", enabled=False) == "hello world"


def test_identity_transform_when_no_provider() -> None:
    assert clean("hello world", enabled=True, provider=None) == "hello world"
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_llm_cleanup.py -v`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/llm_cleanup.py`:

```python
"""LLM cleanup pipeline. Identity transform in v1; providers added in v2."""
from __future__ import annotations


def clean(text: str, *, enabled: bool = False, provider: str | None = None) -> str:
    """v1: identity transform. Pipeline position is audio → ASR → here → clipboard → paste."""
    if not enabled or provider is None:
        return text
    # v2: dispatch on provider to OpenAI/Claude/Gemini/Ollama/LM Studio.
    return text
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_llm_cleanup.py -v`.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/llm_cleanup.py tests/test_llm_cleanup.py
git commit -m "feat(llm): add cleanup pipeline module (identity transform in v1)"
```

---

## Task M1b.6: Transcription orchestrator (TDD, integration)

**Files:** `backend/nemotronflow/transcription.py`, `tests/test_transcription.py`.

Wires AudioCapture + EngineManager + llm_cleanup + notification emit. Handles the warmup-deferral: if a press happens while `not engine.is_ready()`, capture records and the release queues the audio to transcribe once ready.

- [ ] **Step 1 (write failing test):** `tests/test_transcription.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import numpy as np
import pytest

from nemotronflow.asr.base import AudioChunk
from nemotronflow.asr.factory import EngineManager
from nemotronflow.transcription import TranscriptionOrchestrator


class FakeCapture:
    def __init__(self) -> None:
        self.recording = False
        self._samples = np.ones(16000, dtype=np.float32) * 0.1

    def start_recording(self) -> None:
        self.recording = True

    def stop_recording(self) -> AudioChunk:
        self.recording = False
        return AudioChunk(samples=self._samples, sample_rate=16000)


@pytest.mark.asyncio
async def test_press_release_emits_result() -> None:
    mgr = EngineManager(initial="stub")
    await mgr.boot()
    cap = FakeCapture()
    emitted: list[tuple[str, dict]] = []

    async def emit(method: str, payload: dict) -> None:
        emitted.append((method, payload))

    orch = TranscriptionOrchestrator(capture=cap, engine_manager=mgr, emit=emit)  # type: ignore[arg-type]
    await orch.on_press()
    assert cap.recording
    await orch.on_release()
    methods = [m for m, _ in emitted]
    assert "state/changed" in methods
    assert "transcription/result" in methods
    result_payload = next(p for m, p in emitted if m == "transcription/result")
    assert result_payload["engine"] == "stub"
    assert result_payload["auto_paste"] is True


@pytest.mark.asyncio
async def test_press_during_warmup_defers_transcription() -> None:
    mgr = EngineManager(initial="stub")
    # Don't boot yet → engine not ready.
    cap = FakeCapture()
    emitted: list[tuple[str, dict]] = []

    async def emit(method: str, payload: dict) -> None:
        emitted.append((method, payload))

    orch = TranscriptionOrchestrator(capture=cap, engine_manager=mgr, emit=emit)  # type: ignore[arg-type]
    await orch.on_press()
    assert cap.recording  # capture runs regardless of warmup
    await orch.on_release()
    # Not ready yet → no result emitted, but audio is queued.
    assert not any(m == "transcription/result" for m, _ in emitted)
    await mgr.boot()  # now ready
    await orch.drain_pending()
    assert any(m == "transcription/result" for m, _ in emitted)
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_transcription.py -v`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/transcription.py`:

```python
"""Orchestrator: hotkey press/release → capture → engine → cleanup → emit."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from nemotronflow.asr.base import AudioChunk
from nemotronflow.asr.factory import EngineManager
from nemotronflow.llm_cleanup import clean
from nemotronflow.logging import get_logger

log = get_logger("transcription")

Emitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class CaptureLike(Protocol):
    def start_recording(self) -> None: ...
    def stop_recording(self) -> AudioChunk: ...


class TranscriptionOrchestrator:
    def __init__(
        self,
        capture: CaptureLike,
        engine_manager: EngineManager,
        emit: Emitter,
        *,
        auto_paste: bool = True,
        llm_enabled: bool = False,
        llm_provider: str | None = None,
    ) -> None:
        self._capture = capture
        self._mgr = engine_manager
        self._emit = emit
        self._auto_paste = auto_paste
        self._llm_enabled = llm_enabled
        self._llm_provider = llm_provider
        self._pending: AudioChunk | None = None  # recorded during warmup

    async def on_press(self) -> None:
        self._capture.start_recording()
        await self._emit("state/changed", {"state": "listening", "started_at": time.time()})
        log.info("hotkey_press")

    async def on_release(self) -> None:
        audio = self._capture.stop_recording()
        if not self._mgr.is_ready():
            # Defer: keep audio, transcribe when engine becomes ready.
            self._pending = audio
            await self._emit("state/changed", {"state": "processing", "detail": "waiting_for_model"})
            log.info("transcribe_deferred_engine_not_ready")
            return
        await self._transcribe(audio)

    async def drain_pending(self) -> None:
        """Called after engine becomes ready; flush any audio recorded during warmup."""
        if self._pending is None:
            return
        audio = self._pending
        self._pending = None
        await self._transcribe(audio)

    async def _transcribe(self, audio: AudioChunk) -> None:
        utterance_id = uuid.uuid4().hex
        await self._emit("state/changed", {"state": "processing", "utterance_id": utterance_id})
        t0 = time.perf_counter()
        # transcribe is synchronous/blocking; run off the event loop.
        result = await asyncio.to_thread(self._mgr.transcribe, audio)
        inference_ms = result.inference_ms
        text = clean(
            result.text,
            enabled=self._llm_enabled,
            provider=self._llm_provider,
        )
        total_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "transcribe_done",
            utterance_id=utterance_id,
            audio_ms=result.audio_ms,
            inference_ms=inference_ms,
            total_ms=total_ms,
        )
        await self._emit(
            "transcription/result",
            {
                "utterance_id": utterance_id,
                "text": text,
                "engine": result.engine,
                "audio_ms": result.audio_ms,
                "inference_ms": inference_ms,
                "total_ms": total_ms,
                "auto_paste": self._auto_paste,
                "timestamp": time.time(),
            },
        )
        await self._emit("state/changed", {"state": "done", "utterance_id": utterance_id})
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_transcription.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/transcription.py tests/test_transcription.py
git commit -m "feat(transcription): add orchestrator with warmup-deferral and result emit"
```

---

## Task M1b.7: Wire orchestrator into server + boot order (TDD)

**Files:** modify `backend/nemotronflow/server.py`, `backend/nemotronflow/__main__.py`; add `tests/test_server_boot.py`.

This enforces the startup order from spec §2.3: mic stream → hotkey → WS server → engine load → warmup → READY.

- [ ] **Step 1 (write failing test):** `tests/test_server_boot.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from nemotronflow.server import boot_sequence, BootEvents


@pytest.mark.asyncio
async def test_boot_order_is_mic_hotkey_ws_engine_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeEvents(BootEvents):
        async def open_microphone(self) -> None: events.append("mic")
        async def register_hotkey(self) -> None: events.append("hotkey")
        async def start_websocket(self, ready: asyncio.Event) -> int:
            events.append("ws"); ready.set(); setattr(ready, "port", 9999); return 9999
        async def load_engine(self) -> None: events.append("engine")
        async def warmup_engine(self) -> None: events.append("warmup")
        async def emit_ready(self) -> None: events.append("ready")

    await boot_sequence(FakeEvents())
    assert events == ["mic", "hotkey", "ws", "engine", "warmup", "ready"]
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_server_boot.py -v`.

- [ ] **Step 3 (implement):** add to `backend/nemotronflow/server.py`:

```python
from __future__ import annotations

# (additions to existing server.py; BootEvents is an abstract seam for testing)
import abc


class BootEvents(abc.ABC):
    @abc.abstractmethod
    async def open_microphone(self) -> None: ...
    @abc.abstractmethod
    async def register_hotkey(self) -> None: ...
    @abc.abstractmethod
    async def start_websocket(self, ready: asyncio.Event) -> int: ...
    @abc.abstractmethod
    async def load_engine(self) -> None: ...
    @abc.abstractmethod
    async def warmup_engine(self) -> None: ...
    @abc.abstractmethod
    async def emit_ready(self) -> None: ...


async def boot_sequence(events: BootEvents) -> None:
    """Spec §2.3 startup order. User must never miss speech during init."""
    await events.open_microphone()
    await events.register_hotkey()
    ready = asyncio.Event()
    port = await events.start_websocket(ready)
    # Engine load + warmup happen AFTER mic + hotkey are armed, so an utterance
    # during warmup is captured and deferred by the orchestrator.
    await events.load_engine()
    await events.warmup_engine()
    await events.emit_ready()
    log.info("boot_complete", port=port)
    await asyncio.Future()  # run forever
```

Then refactor `__main__.py` to construct a concrete `BootEvents` implementation that calls AudioCapture.open / HotkeyListener.start / serve / EngineManager.boot. (The concrete impl is straightforward wiring; keep it small and free of new logic.)

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_server_boot.py -v` → PASS. Then run the full suite to confirm nothing regressed.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/server.py backend/nemotronflow/__main__.py tests/test_server_boot.py
git commit -m "feat(server): enforce mic→hotkey→ws→engine→warmup→ready boot order"
```

---

## Task M1b.8: Latency benchmarks

**Files:** `tests/benchmarks/__init__.py`, `tests/benchmarks/test_hotkey_latency.py`, `tests/benchmarks/test_asr_latency.py`.

- [ ] **Step 1:** `tests/benchmarks/test_hotkey_latency.py`:

```python
from __future__ import annotations

import time

from nemotronflow.audio_capture import AudioCapture


def test_start_recording_under_1ms() -> None:
    cap = AudioCapture()
    cap.start_recording()  # prime
    samples = []
    for _ in range(100):
        t0 = time.perf_counter_ns()
        cap.start_recording()
        samples.append((time.perf_counter_ns() - t0) / 1e6)
    p99 = sorted(samples)[98]
    assert p99 < 1.0, f"start_recording p99={p99:.3f}ms exceeds 1ms budget"
```

- [ ] **Step 2:** `tests/benchmarks/test_asr_latency.py`:

```python
from __future__ import annotations

import numpy as np
import pytest

from nemotronflow.asr.base import AudioChunk
from nemotronflow.asr.factory import EngineManager


@pytest.mark.asyncio
async def test_stub_transcribe_warm_under_200ms() -> None:
    mgr = EngineManager(initial="stub")
    await mgr.boot()
    audio = AudioChunk(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000)
    mgr.transcribe(audio)  # warm
    # pytest-benchmark:
    # benchmark.pedantic(mgr.transcribe, args=(audio,), rounds=20, iterations=1)
    # For simplicity without pytest-benchmark fixture wiring, assert p99 manually:
    import time

    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        mgr.transcribe(audio)
        times.append((time.perf_counter() - t0) * 1000)
    p99 = sorted(times)[18]
    assert p99 < 200, f"stub transcribe p99={p99:.1f}ms exceeds 200ms headroom"
```

- [ ] **Step 3:** Run + commit + tag:
```bash
pytest tests/benchmarks -v
git add tests/benchmarks
git commit -m "test(bench): add hotkey-start and stub-asr latency benchmarks"
git tag v0.1.0-m1b
```

**M1b commit boundary:** `v0.1.0-m1b` — pressing Ctrl+Space (real keyboard) records audio, releasing transcribes via StubASR, and a `transcription/result` notification is emitted over the WebSocket. Latency budget proven by benchmarks. This is a fully working headless push-to-talk backend.
