# DECISIONS.md

> Record of every important engineering decision. Never remove previous entries.

---

## D-001: Architecture B (Pure Tauri + Rust) over Architecture A (Python Sidecar)

**Date**: 2026-06-25
**Context**: The original spec called for a Python sidecar running NeMo/PyTorch, communicating with a Tauri shell via JSON-RPC/WebSocket IPC. The question: is a Python sidecar actually required?
**Alternatives considered**:
- A: Python sidecar (NeMo/PyTorch) + Tauri shell + JSON-RPC/WebSocket IPC
- B: Pure Tauri + Rust `ort` crate (ONNX Runtime bindings)
**Decision**: Architecture B — proceed with ONNX export validation spike before committing.
**Reasoning**: Eliminating Python reduces bundle size (no PyTorch/NeMo wheels), startup time (no Python interpreter), IPC complexity, and deployment headaches. Precedent exists: `parakeet-rs` and `canary-rs` already run sibling models in pure Rust.
**Impact**: Requires validating ONNX export path before rewriting the spec.

---

## D-002: Python 3.11 instead of Python 3.14

**Date**: 2026-06-25
**Context**: System Python is 3.14. PyTorch has no pre-built wheels for 3.14.
**Alternatives considered**:
- Build PyTorch from source for 3.14 (too slow, fragile)
- Use Python 3.12 or 3.13
- Use Python 3.11 (has mature PyTorch wheels)
**Decision**: Install Python 3.11.0 separately.
**Reasoning**: PyTorch 2.7.0 has reliable wheels for Python 3.11 on Windows. Python 3.11 is also the most tested with NeMo 2.7.3.
**Impact**: Spike scripts must reference full path to Python 3.11 executable.

---

## D-003: numpy 1.26.x required

**Date**: 2026-06-25
**Context**: NeMo 2.7.3 segfaults during import with numpy 2.x.
**Alternatives considered**:
- Wait for NeMo update supporting numpy 2.x
- Pin numpy to 1.26.x
**Decision**: Pin numpy==1.26.4.
**Reasoning**: numpy 1.26.4 is the last 1.x release and works reliably with NeMo. Some pyannote packages complain but NeMo functions correctly.
**Impact**: Any environment for this spike must have numpy<2.0.

---

## D-004: CUDA_VISIBLE_DEVICES="" for model operations on Windows

**Date**: 2026-06-25
**Context**: NeMo `restore_from` segfaults when CUDA is available on Windows (driver 596.36, CUDA 13.2). The model card lists "Preferred/Supported OS: Linux" — Windows is not officially supported.
**Alternatives considered**:
- Fix the CUDA/driver/NeMo compatibility issue
- Use WSL2
- Disable CUDA entirely and use CPU only
**Decision**: Set `CUDA_VISIBLE_DEVICES=""` and `map_location="cpu"` for all NeMo operations. The ONNX export can still produce models that `ort` loads with CUDA EP.
**Reasoning**: The segfault is a NeMo/CUDA/driver compatibility issue. ONNX Runtime has its own CUDA integration that may work independently. The GPU segfault affects Architecture A equally — if NeMo can't load on Windows+GPU, the Python sidecar is also broken.
**Impact**: All spike scripts must set CUDA_VISIBLE_DEVICES before importing torch/nemo.

---

## D-005: torch.cuda.is_available() before nemo import (segfault workaround)

**Date**: 2026-06-26
**Context**: `import nemo.collections.asr` segfaults deterministically on this Windows+CUDA environment unless `torch.cuda.is_available()` is called first.
**Alternatives considered**:
- Debug the root cause in NeMo/megatron/apex init
- Use a wrapper script that pre-initializes CUDA
**Decision**: Always call `torch.cuda.is_available()` before importing nemo.
**Reasoning**: This triggers CUDA runtime initialization in a controlled way. Deterministic: scripts without this call always segfault (3/3), scripts with it always succeed (3/3). The root cause is likely NeMo's megatron/apex imports triggering CUDA lazy loading in an unsafe way.
**Impact**: Every spike script must include this pattern. Future Rust ort code won't need this (no NeMo dependency).

---

## D-006: Non-streaming ONNX export first, streaming second

**Date**: 2026-06-26
**Context**: The model supports both non-streaming and cache-aware streaming export. Streaming adds cache tensor complexity.
**Alternatives considered**:
- Export streaming only (more complex, more useful for final app)
- Export both simultaneously
- Export non-streaming first to validate the path, then streaming
**Decision**: Non-streaming first (Phase 2a), then streaming (Phase 2b).
**Reasoning**: Non-streaming is simpler to validate and proves the export path works. Streaming adds cache tensors which are an orthogonal concern.
**Impact**: Both exported successfully. Non-streaming has 2 inputs (audio_signal, length); streaming adds 3 cache inputs and 3 cache outputs.

---

## D-007: Feature extraction will remain in Rust (not exported to ONNX)

**Date**: 2026-06-26
**Context**: NeMo's preprocessor (mel spectrogram) could potentially be exported to ONNX, but the audio-to-mel pipeline is simple enough to implement in Rust.
**Alternatives considered**:
- Export preprocessor to ONNX too
- Implement mel spectrogram in Rust
- Use a Rust audio processing library
**Decision**: Implement in Rust (not yet done — pending).
**Reasoning**: The preprocessor is a straightforward pipeline (windowing, FFT, mel filterbank, log). Implementing it in Rust avoids an extra ONNX model load and is faster for real-time audio. The Rust `rustdct` or `hound` + custom mel filterbank is sufficient.
**Impact**: Need to port `AudioToMelSpectrogramPreprocessor` parameters: 128 mel bands, 25ms Hann window, 10ms stride, 512 FFT, 16kHz sample rate.

---

## D-008: SentencePiece tokenizer will be loaded directly in Rust

**Date**: 2026-06-26
**Context**: The model uses a BPE tokenizer (SentencePiece) with 1024 tokens. After RNNT decode produces token IDs, we need to convert to text.
**Alternatives considered**:
- Export the tokenizer to ONNX
- Use Python's sentencepiece library (requires Python)
- Use Rust `sentencepiece` crate
**Decision**: Load the SentencePiece model directly in Rust.
**Reasoning**: The `sentencepiece` crate provides native Rust bindings. The tokenizer model is a small file (~50 KB) embedded in the .nemo archive. No need for Python or ONNX.
**Impact**: Need to extract the SentencePiece model from the .nemo file. The model is at `model.tokenizer.tokenizer` (a `SentencePieceTokenizer` wrapping a sp model).
