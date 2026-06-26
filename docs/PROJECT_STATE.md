# PROJECT_STATE.md

> **Always keep this file updated.** This is the single source of truth for the current project state.

## Project: NemotronFlow

An open-source desktop app providing a premium Wispr Flow push-to-talk speech-to-text experience, powered by NVIDIA Nemotron 3.5 ASR (`nvidia/nemotron-speech-streaming-en-0.6b`).

## Current Milestone

**Architecture Validation Spike** — determining if the ASR model can be exported to ONNX and run through Rust's `ort` crate to eliminate the Python sidecar dependency.

## Current Spike Phase

**Phase 2c (complete)** — ONNX export complete, encoder proven correct (max diff 6e-6), decoder_joint argmax proven correct, full greedy decode pipeline produces ~90% word-match transcriptions. Token-level differences from NeMo reference are due to NeMo's label-looping heuristics not replicated in simple frame-looping greedy. Next: Phase 3 (Rust ort validation).

## Overall Progress

| Phase | Status | Notes |
|-------|--------|-------|
| M0: Scaffold | ✅ Complete | 5 commits on `main`, tags `v0.1.0-m0-scaffold` and `v0.1.0-m0` |
| Typr Evaluation | ✅ Complete | 16% coverage, not worth pivoting |
| Spike Phase 0: Prerequisites | ✅ Complete | Rust 1.96.0, Python 3.11, PyTorch 2.7.0, NeMo 2.7.3 |
| Spike Phase 1: Model download | ✅ Complete | 2.4 GB .nemo file from HuggingFace |
| Spike Phase 2a: Non-streaming ONNX export | ✅ Complete | encoder.onnx + decoder_joint.onnx, verified with onnxruntime |
| Spike Phase 2b: Streaming ONNX export | ✅ Complete | With cache tensors, ~2.4 GB total |
| Spike Phase 2c: End-to-end validation | ✅ Complete | Encoder proven correct, decoder_joint argmax matches, full pipeline works |
| Spike Phase 3: Rust ort validation | ⏳ Pending | |
| Spike Phase 4: Benchmarking | ⏳ Pending | |
| Spike Phase 5: Streaming feasibility | ⏳ Pending | |
| Spike Phase 6: Final report | ⏳ Pending | |

**Overall: ~55% of spike complete**

## Current Blocker

None. Phase 2 is complete. Phase 3 (Rust ort) is ready to begin.

## Last Successful Validation

- **Encoder ONNX vs PyTorch**: max absolute difference = 6e-6, allclose at 1e-4
- **ONNX encoder + NeMo decoder**: produces EXACT reference transcript
- **Decoder_joint ONNX vs PyTorch**: argmax matches for every tested (frame, token) pair
- **Full ONNX pipeline**: produces recognizable transcriptions (~90% word match)

## Current Architecture Status

Architecture B (Pure Tauri + Rust ort) is the leading candidate, recommended on 7/7 evaluation axes. This spike validates the ONNX export path that Architecture B requires.

## Spike Workspace Location

The spike lives at `C:\Users\Administrator\ZCodeProject\spike\` (outside the NemotronFlow repo). It contains:
- `models/nemotron-speech-streaming-en-0.6b.nemo` — 2.4 GB model file
- `models/onnx/` — exported non-streaming ONNX (encoder + decoder_joint + weights)
- `models/onnx/streaming/` — exported streaming ONNX (with cache tensors)
- `audio/` — test samples (sample1.flac, sample2.flac)
- `scripts/` — 18+ Python validation scripts
- `rust-validator/` — empty, awaiting Phase 3

## Environment

- **OS**: Windows 10, Build 26200 (x64)
- **GPU**: NVIDIA RTX 3090 Ti, 24 GB VRAM, Driver 596.36, CUDA 13.2
- **Python**: 3.11.0 at `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`
- **Rust**: 1.96.0 at `C:\Users\Administrator\.cargo\bin\`
- **PyTorch**: 2.7.0+cu131 | **NeMo**: 2.7.3 | **numpy**: 1.26.4 (1.26.x required)
- No CUDA Toolkit (nvcc) installed — ort crate uses prebuilt CUDA libs via `load-dynamic`
- No ffmpeg installed

## Important Spike Scripts

| Script | Purpose |
|--------|---------|
| `spike/scripts/04d_repro.py` | Minimal model load test (working reference for segfault fix) |
| `spike/scripts/04e_export_nonstreaming.py` | Exports non-streaming ONNX (encoder + decoder_joint) |
| `spike/scripts/04f_export_streaming.py` | Exports streaming ONNX (with cache tensors) |
| `spike/scripts/13_compare_pipeline.py` | Encoder ONNX vs PyTorch tensor comparison |
| `spike/scripts/16_softmax_verify.py` | Decoder_joint raw-logits vs log_softmax verification |
| `spike/scripts/17_full_onnx_pipeline.py` | Full ONNX greedy RNNT decode → transcript |
| `spike/scripts/18_decode_compare.py` | Token-by-token comparison of ONNX vs NeMo decode |

## Next Immediate Objective

**Spike Phase 3**: Create Rust project in `spike/rust-validator/`, load ONNX models via `ort` crate, run inference on test audio, compare transcript against reference.

## Known Technical Discoveries

1. **CUDA segfault workaround**: On this Windows+CUDA environment, `import nemo.collections.asr` segfaults unless `torch.cuda.is_available()` is called first. Set `CUDA_VISIBLE_DEVICES=""` to load model on CPU.
2. **numpy 1.26.x required**: numpy 2.x causes NeMo to segfault during import.
3. **ONNX decoder_joint returns raw logits**: The exported model disables log_softmax. Argmax is identical to PyTorch (which applies log_softmax on CPU).
4. **ONNX state output indexing**: decoder_joint outputs are `[logits, prednet_lengths, output_states_1, output_states_2]` — states at indices 2 and 3, not 1 and 2.
5. **ONNX weights stored as external files**: The .onnx files are small (~2–34 MB); actual weights are separate files in the same directory (~2.4 GB total). onnxruntime loads them transparently.
6. **Model GPU load segfaults on Windows**: NeMo `restore_from` segfaults when CUDA is available. CPU load works. This is a NeMo/CUDA/driver compatibility issue, not an ONNX issue.
7. **Encoder layout in ONNX**: `[B, D=1024, T]` (hidden dim in middle position).
8. **Blank token index**: 1024 (last, equals vocab_size). SentencePiece vocab: 1024 tokens.
