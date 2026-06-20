# M5 — NemotronASR (NeMo) + EngineManager lifecycle + warmup + model download

**Goal:** Replace the stub speech with real NVIDIA Nemotron 3.5 ASR via the NeMo framework. The `NemotronASR` engine loads a configurable `.nemo` checkpoint (default name `nemotron-asr-3.5`), warms up at startup, transcribes within the latency budget on GPU, and downloads its checkpoint lazily with resumable `download/progress` events. After M5, speaking produces real transcribed text.

**A note on the model name:** the product is branded around "Nemotron 3.5 ASR." The `NemotronASR` implementation loads whatever NeMo ASR checkpoint path the user configures; the default checkpoint identifier is the string `nemotron-asr-3.5`. The `BaseASR` plugin layer means the wiring is identical regardless of which NeMo checkpoint is loaded — the day a literal `nemotron-asr-3.5.nemo` ships, it's a one-line config change.

**Files to create/modify:**
- `backend/nemotronflow/asr/nemotron.py` — `NemotronASR`.
- `backend/nemotronflow/asr/factory.py` — register `nemotron` in `ENGINE_REGISTRY`; `EngineManager` emits `model/progress` + `download/progress`.
- `backend/nemotronflow/model_download.py` — resumable HTTP downloader with progress callbacks.
- `tests/test_nemotron_asr.py` — `@pytest.mark.gpu` real path; non-GPU uses a fixture `.nemo` stub that asserts the load/transcribe plumbing (mocked NeMo).
- `tests/test_model_download.py` — resumable download with a local HTTP server + interrupted resume.
- `backend/nemotronflow/models/.gitkeep`.

**Dependencies:** M4. Optional `nemo-toolkit[asr]` extra: `pip install -e ".[nemo]"`. A CUDA GPU is strongly recommended; CPU works but misses the latency budget (the overlay shows `CPU MODE`).

**Acceptance criteria:**
- `NemotronASR` passes `test_engine_contract.py` (same assertions as StubASR).
- Warm `transcribe()` on GPU ≤ 300 ms for a typical 3–5 s utterance (asserted in the `@pytest.mark.gpu` latency test; logged otherwise).
- Model loads at startup via `load()`, warms via `warmup()`, passes `health()` → `model/status` ready.
- Engine switching (`engine/set`) unloads → frees → loads → warms → emits ready. Never two engines warm.
- First run with no checkpoint: triggers a resumable download emitting `download/progress`; an interrupted download resumes from the byte offset.
- `device()` returns `cuda` or `cpu` truthfully; the overlay badge reflects it.
- If NeMo is not installed, `NemotronASR.load()` raises a clear `RuntimeError` with install instructions, and the app falls back to StubASR with a tray notification.

**Test strategy:** `test_nemotron_asr.py` is split: a mocked-NeMo unit test (always runs, asserts load/warmup/transcribe plumbing + health) and a `@pytest.mark.gpu` test (real NeMo + real checkpoint, asserts latency, runs on the nightly GPU runner only). `test_model_download.py` uses Python's `http.server` to serve bytes and asserts resume-after-interrupt. `test_engine_contract.py` from M1b now includes `NemotronASR` in its parametrize list (gated by `pytest.mark.gpu`).

**Estimated complexity:** High. NeMo packaging + checkpoint acquisition + GPU latency are the hard parts.

**Commit boundaries:** one per task, tag `v0.1.0-m5`.

---

## Task M5.1: Resumable model downloader (TDD)

**Files:** `backend/nemotronflow/model_download.py`, `tests/test_model_download.py`.

- [ ] **Step 1 (write failing test):** `tests/test_model_download.py`:

```python
from __future__ import annotations

import asyncio
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from nemotronflow.model_download import download


@pytest.fixture
def http_server(tmp_path: Path):
    payload = b"x" * (1024 * 100)
    (tmp_path / "model.nemo").write_bytes(payload)
    handler = lambda *a, **kw: SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **kw)
    srv = HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/model.nemo", payload
    srv.shutdown()


@pytest.mark.asyncio
async def test_download_completes(http_server, tmp_data_dir: Path) -> None:
    url, payload = http_server
    dest = tmp_data_dir / "models" / "model.nemo"
    progress: list[int] = []
    await download(url, dest, on_progress=lambda p: progress.append(p))
    assert dest.read_bytes() == payload
    assert progress[-1] == 100


@pytest.mark.asyncio
async def test_download_resumes_after_interrupt(http_server, tmp_data_dir: Path) -> None:
    url, payload = http_server
    dest = tmp_data_dir / "models" / "model.nemo"
    # Pre-write a partial file (first 30%).
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload[: len(payload) // 3])
    await download(url, dest, on_progress=lambda _p: None)
    assert dest.read_bytes() == payload
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_model_download.py -v`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/model_download.py` — async downloader using `aiohttp` (add to deps) that sends `Range: bytes=<existing>-` when the dest exists, appends to the file, computes percent from `Content-Range`, calls `on_progress(percent)` periodically, and verifies final size matches. Retries on transient errors with backoff.

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_model_download.py -v`.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/model_download.py tests/test_model_download.py pyproject.toml
git commit -m "feat(models): add resumable model downloader with progress callbacks"
```

---

## Task M5.2: NemotronASR engine (TDD — mocked NeMo)

**Files:** `backend/nemotronflow/asr/nemotron.py`, `tests/test_nemotron_asr.py`.

- [ ] **Step 1 (write failing test):** `tests/test_nemotron_asr.py`:

```python
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from nemotronflow.asr.base import AudioChunk


def _install_fake_nemo(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Inject a fake nemo module so the test runs without the real package."""
    state = {"loaded_path": None, "warmup_calls": 0}

    class FakeModel:
        def __init__(self): state["loaded_path"] = "fake"
        def transcribe(self, paths, **kw): return ["fake nemotron transcript"]
    class FakeEncDec:
        @classmethod
        def from_pretrained(cls, path): return FakeModel()
    nemo = types.ModuleType("nemo")
    nemo_asr = types.ModuleType("nemo.collections.asr")
    nemo_asr_models = types.ModuleType("nemo.collections.asr.models")
    nemo_asr_models.EncDecCTCModelBPE = FakeEncDec
    nemo_asr.models = nemo_asr_models
    nemo.collections = types.SimpleNamespace(asr=nemo_asr)
    monkeypatch.setitem(sys.modules, "nemo", nemo)
    monkeypatch.setitem(sys.modules, "nemo.collections", nemo.collections)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr", nemo_asr)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr.models", nemo_asr_models)
    return state


def test_nemotron_loads_and_transcribes(monkeypatch, tmp_path: Path) -> None:
    _install_fake_nemo(monkeypatch)
    from nemotronflow.asr.nemotron import NemotronASR
    eng = NemotronASR(checkpoint_path=str(tmp_path / "nemotron-asr-3.5.nemo"), device="cpu")
    eng.load(); eng.warmup()
    assert eng.is_ready()
    result = eng.transcribe(AudioChunk(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000))
    assert result.engine == "nemotron"
    assert "transcript" in result.text
    assert eng.device() in ("cpu", "cuda")


def test_nemotron_clear_error_when_nemo_missing(monkeypatch) -> None:
    # Ensure nemo is NOT importable.
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **kw):
        if name.startswith("nemo"):
            raise ImportError(f"simulated: no module named '{name}'")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    from nemotronflow.asr.nemotron import NemotronASR
    eng = NemotronASR(checkpoint_path="x.nemo", device="cpu")
    with pytest.raises(RuntimeError, match="nemo-toolkit"):
        eng.load()
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_nemotron_asr.py -v`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/asr/nemotron.py`:

```python
"""NemotronASR — production engine backed by NVIDIA NeMo.

Loads a configurable .nemo checkpoint (default identifier 'nemotron-asr-3.5').
Because the BaseASR plugin layer is engine-agnostic, swapping in a different
NeMo ASR checkpoint is a one-line config change.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from nemotronflow.asr.base import AudioChunk, BaseASR, TranscriptionResult
from nemotronflow.logging import get_logger

log = get_logger("nemotron_asr")


class NemotronASR(BaseASR):
    def __init__(self, *, checkpoint_path: str, device: str = "auto", precision: str = "fp16", language: str = "en") -> None:
        self._checkpoint = Path(checkpoint_path)
        self._device_requested = device
        self._precision = precision
        self._language = language
        self._model: Any = None
        self._device: str = "cpu"
        self._ready = False

    def name(self) -> str:
        return "nemotron"

    def _resolve_device(self) -> str:
        if self._device_requested != "auto":
            return self._device_requested
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            return "cpu"

    def load(self) -> None:
        try:
            from nemo.collections.asr.models import EncDecCTCModelBPE  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "nemo-toolkit is not installed. Install with: pip install -e '.[nemo]'"
            ) from e
        if not self._checkpoint.exists():
            raise RuntimeError(f"checkpoint not found: {self._checkpoint}. Configure nemotron_checkpoint_path or run the model download.")
        self._device = self._resolve_device()
        self._model = EncDecCTCModelBPE.from_pretrained(str(self._checkpoint))
        if self._device == "cuda":
            try:
                self._model = self._model.to(self._device)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                self._device = "cpu"
        self._ready = True
        log.info("nemotron_loaded", checkpoint=str(self._checkpoint), device=self._device, precision=self._precision)

    def unload(self) -> None:
        self._model = None
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    def warmup(self) -> None:
        if not self._ready:
            return
        silence = AudioChunk(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000)
        self.transcribe(silence)

    def transcribe(self, audio: AudioChunk) -> TranscriptionResult:
        if not self._ready:
            raise RuntimeError("NemotronASR not loaded")
        import soundfile as sf  # local import; heavy
        tmp = Path(f"/tmp/nf_{uuid.uuid4().hex}.wav") if Path("/tmp").exists() else Path.cwd() / f"nf_{uuid.uuid4().hex}.wav"
        try:
            sf.write(str(tmp), audio.samples.astype(np.float32), audio.sample_rate)
            t0 = time.perf_counter()
            outputs = self._model.transcribe([str(tmp)])  # type: ignore[union-attr]
            inference_ms = int((time.perf_counter() - t0) * 1000)
            text = outputs[0] if outputs else ""
        finally:
            tmp.unlink(missing_ok=True)
        audio_ms = int(len(audio.samples) / audio.sample_rate * 1000)
        return TranscriptionResult(
            text=text, engine=self.name(), audio_ms=audio_ms, inference_ms=inference_ms, language=self._language
        )

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name(),
            "device": self._device,
            "precision": self._precision,
            "checkpoint": str(self._checkpoint),
            "ready": self._ready,
        }

    def device(self) -> str:
        return self._device

    def supports_languages(self) -> list[str]:
        return [self._language]
```

(Add `soundfile` to runtime deps; it's a NeMo companion anyway.)

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_nemotron_asr.py -v`.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/asr/nemotron.py tests/test_nemotron_asr.py pyproject.toml
git commit -m "feat(asr): add NemotronASR engine backed by NeMo with clear install errors"
```

---

## Task M5.3: Register NemotronASR + contract test parity

**Files:** modify `backend/nemotronflow/asr/factory.py`, `tests/test_engine_contract.py`.

- [ ] **Step 1:** In `factory.py`, add `"nemotron": lambda: NemotronASR(checkpoint_path=settings.nemotron_checkpoint_path, device=settings.nemotron_device, precision=settings.nemotron_precision, language=settings.nemotron_language)` to `ENGINE_REGISTRY` — but construct lazily and guard the import so the app still boots without `nemo` installed (the factory raises a clear error only when `nemotron` is actually selected).

- [ ] **Step 2:** Update `tests/test_engine_contract.py` to include a GPU-marked Nemotron parametrization (skipped unless `nemo` importable + a fixture checkpoint env var is set).

- [ ] **Step 3:** Add a real-checkpoint latency test `tests/test_nemotron_asr_latency.py` marked `@pytest.mark.gpu` asserting warm `transcribe()` ≤ 300 ms on a 3 s fixture utterance.

- [ ] **Step 4:** Commit:
```bash
git add backend/nemotronflow/asr/factory.py tests/test_engine_contract.py tests/test_nemotron_asr_latency.py
git commit -m "test(asr): add NemotronASR to engine contract + gpu latency benchmark"
```

---

## Task M5.4: EngineManager download wiring + status/progress events

**Files:** modify `backend/nemotronflow/asr/factory.py`, `server.py`.

- [ ] **Step 1:** When the configured checkpoint is missing on boot or `engine/set nemotron`, `EngineManager` triggers `model_download.download(...)` with `on_progress` → `emit("download/progress", {model, percent, bytes, bytes_total})` and `emit("model/progress", {phase: "downloading", percent})`. `load()` is gated on download completion.

- [ ] **Step 2:** `EngineManager.boot()` now: if NeMo isn't importable OR checkpoint missing AND `future_download_models` is false, fall back to StubASR and emit `model/status {phase: "error", detail: "nemotron unavailable, using stub"}`. This keeps first-run usable.

- [ ] **Step 3 (test):** extend `tests/test_engine_manager.py` to assert that with no checkpoint + downloads disabled, boot falls back to stub and emits the error status.

- [ ] **Step 4:** Commit:
```bash
git add backend/nemotronflow/asr/factory.py backend/nemotronflow/server.py tests/test_engine_manager.py
git commit -m "feat(asr): wire model download + fallback-to-stub + progress events"
```

---

## Task M5.5: Manual acceptance + tag

- [ ] **Step 1 (manual, GPU machine):** With NeMo installed + a real checkpoint configured, boot the app. Confirm overlay badge shows `CUDA`. Hold Ctrl+Space, say a real sentence, release — confirm real transcribed text pastes into a target app within the latency budget. Switch engine to stub and back via Settings → confirm unload/load/ready sequence.

- [ ] **Step 2 (CPU fallback):** With no GPU, confirm badge shows `CPU MODE` and speech still works (slower), with no crash.

- [ ] **Step 3 (full suite):** `make lint && make test` (GPU tests skipped locally, run in nightly CI).

- [ ] **Step 4:** Commit + tag:
```bash
git add -A
git commit -m "feat(m5): real Nemotron speech working on GPU with stub fallback (manual acceptance passed)"
git tag v0.1.0-m5
```

**M5 commit boundary:** `v0.1.0-m5` — the app does real speech-to-text. This is the moment it stops being a demo and becomes the product.
