# SPIKE_REPORT.md

> Chronological history of every spike experiment. Never delete previous entries.

---

## Spike: Architecture Validation — ONNX Export Feasibility

**Date**: 2026-06-25 to 2026-06-26
**Goal**: Determine if `nvidia/nemotron-speech-streaming-en-0.6b` can be exported to ONNX and run through Rust's `ort` crate, validating Architecture B (pure Tauri + Rust) before committing to a rewrite.
**Model**: `nvidia/nemotron-speech-streaming-en-0.6b` — Cache-Aware Streaming FastConformer-RNNT, 618M params, WER 2.32% on LibriSpeech test-clean.

### Phase 0: Prerequisites

- Installed Rust 1.96.0, Python 3.11.0 (system Python 3.14 too new for PyTorch wheels)
- Installed PyTorch 2.7.0+cu131, NeMo 2.7.3, onnxruntime
- Environment: RTX 3090 Ti 24GB, Windows 10, CUDA 13.2, no CUDA Toolkit

**Problems**: Python 3.14 had no PyTorch wheels. NeMo pip install timed out (multiple retries needed).

### Phase 1: Model Download

- Downloaded `nemotron-speech-streaming-en-0.6b.nemo` (2,473,041,920 bytes) from HuggingFace
- Also downloaded LibriSpeech test samples (`sample1.flac`, `sample2.flac`)

### Phase 2a: Non-Streaming ONNX Export

**Script**: `spike/scripts/04e_export_nonstreaming.py`

**Discovery — Segfault Workaround**:
- `import nemo.collections.asr` segfaults on Windows unless `torch.cuda.is_available()` is called first
- Root cause: NeMo's import triggers lazy CUDA initialization that conflicts with the Windows driver. Calling `torch.cuda.is_available()` first initializes CUDA in a controlled way.
- Deterministic: scripts without this call always segfault (3/3); scripts with it always succeed (3/3).
- This is a NeMo/Windows/CUDA compatibility issue, NOT an ONNX issue.

**Export Method**: `model.export(output="encoder.onnx")` — NeMo's `Exportable` class:
- Iterates over `model.list_export_subnets()` → `['encoder', 'decoder_joint']`
- Produces two ONNX files: `encoder-encoder.onnx` (graph, ~2 MB) + `decoder_joint-encoder.onnx` (~34 MB)
- Weights stored as external files (~2.4 GB total) due to protobuf 2GB limit
- Default: `cache_support=False` (no streaming cache tensors)
- Must set `CUDA_VISIBLE_DEVICES=""` before import (GPU load segfaults on Windows)

**Result**: ✅ Both ONNX files export successfully, load in onnxruntime CPUExecutionProvider

**Encoder ONNX inputs/outputs**:
- IN: `audio_signal [B, 128, T]` (float), `length [B]` (int64)
- OUT: `outputs [B, 1024, T]` (float), `encoded_lengths [B]` (int64)

**Decoder ONNX inputs/outputs**:
- IN: `encoder_outputs [B, 1024, T]` (float), `targets [B, U]` (int32), `target_length [B]` (int32), `input_states_1 [2, B, 640]` (float), `input_states_2 [2, B, 640]` (float)
- OUT: `outputs [B, T, U, 1025]` (float), `prednet_lengths [B]` (int32), `output_states_1 [2, B, 640]` (float), `output_states_2 [2, B, 640]` (float)

### Phase 2b: Streaming ONNX Export

**Script**: `spike/scripts/04f_export_streaming.py`

- Set `model.set_export_config({"cache_support": True})` before export
- Encoder ONNX gains cache tensor inputs/outputs:
  - IN: `cache_last_channel [B, 24, T, 1024]`, `cache_last_time [B, 24, 1024, T]`, `cache_last_channel_len [B]`
  - OUT: `cache_last_channel_next`, `cache_last_time_next`, `cache_last_channel_next_len`
- 24 = number of cached encoder layers (Cache-Aware Streaming FastConformer)

**Result**: ✅ Streaming ONNX exports and loads in onnxruntime

### Phase 2c-1: Encoder ONNX Validation

**Script**: `spike/scripts/13_compare_pipeline.py`

- Compared ONNX encoder output vs PyTorch encoder output for identical mel features
- **Max absolute difference: 6e-6** (float32 precision)
- Fed ONNX encoder features into NeMo's native decoder → **EXACT reference transcript**
- **Conclusion**: ONNX encoder is mathematically correct

### Phase 2c-2: Decoder_Joint ONNX Validation

**Script**: `spike/scripts/16_softmax_verify.py`

- Compared ONNX decoder_joint logits vs PyTorch (predict net + joint net) for identical inputs
- **Discovery**: ONNX returns raw logits; PyTorch applies `log_softmax` on CPU
- Difference = constant offset per (T,U) position (= `logsumexp(logits)`)
- `manual_log_softmax(ONNX_logits)` matches PyTorch logits within 7.6e-5
- **Argmax is identical for every tested (frame, token) pair**
- **Conclusion**: ONNX decoder_joint is mathematically correct

### Phase 2c-3: Full ONNX Pipeline (First Attempt)

**Script**: `spike/scripts/17_full_onnx_pipeline.py`

- Implemented greedy RNNT decode loop: outer loop over encoder frames T, inner loop emits tokens until blank
- SOS/priming: feed `blank_id` as first token with zero LSTM states
- **Result**: Pipeline produces recognizable transcriptions (~90% word match with reference)
- **Mismatch**: Missing words, merged tokens, capitalization loss

### Phase 2c-4: Root Cause Investigation and FIX

**Scripts**: `spike/scripts/19_init_state.py`, `21_frame_frame.py`, `22_fixed_decode.py`

**Investigation steps**:
1. Script 19 — Tested whether `initialize_state()` returns encoder-derived state (it returns zeros — not the cause)
2. Script 21 — Frame-by-frame argmax comparison with identical inputs (SOS, zero state): **ONNX and PyTorch agree at ALL frames** — proving the ONNX models are correct
3. Studied NeMo's `GreedyBatchedRNNTLabelLoopingComputer.torch_impl` source code

**Root cause found**: The decode loop was updating the LSTM state on **every** decoder_joint call, including when blank was predicted. But NeMo's label-looping algorithm does NOT update the prediction-network state when advancing through blank frames. It calls `predict()` once, reuses that prediction output across multiple encoder frames (only advancing the time index on blank), and only calls `predict()` again after a non-blank token is emitted.

Since the ONNX decoder_joint re-runs `predict()` on every call, feeding it the **updated** state after a blank frame produces a different prediction than NeMo's reused prediction. The fix: **do not update the state when blank is predicted** — keep the previous state for the next frame. Because `predict(last_token, old_state)` is deterministic, re-running it on the next frame with the same inputs gives the same prediction, matching NeMo's reuse pattern.

**Fix** (`spike/scripts/22_fixed_decode.py`):
```python
if pred == blank_id:
    # Do NOT update state — keep previous state for next frame
    not_blank = False
else:
    # Non-blank: update state and token
    emitted.append(pred)
    last_token = pred
    state1 = new_state1
    state2 = new_state2
```

**Result**: ✅ **BYTE-FOR-BYTE EXACT MATCH** on both sample1.flac and sample2.flac against native NeMo transcription.

```
sample1.flac:
  ONNX:  Going along slushy country roads and speaking to damp audiences in draughtty
         schoolrooms day after day for a fortnight he'll have to put in an appearance
         at some place of worship on Sunday morning and he can come to us immediately afterwards.
  Ref:   [identical]
  EXACT MATCH: True

sample2.flac:
  ONNX:  Before he had time to answer, a much encumbered Vera burst into the room with the
         question, I say, can I leave these here? These were a small black pig and a lusty
         specimen of black red gamecock.
  Ref:   [identical]
  EXACT MATCH: True
```

**Spike Phase 2: COMPLETE.** The full ONNX pipeline (audio → features → encoder.onnx → decoder_joint.onnx → greedy RNNT decode → SentencePiece) produces identical output to native NeMo.

### Failed Hypotheses

1. **`to_onnx()` method exists on the model**: Does not exist. The correct method is `export()` with `.onnx` file extension.
2. **`predict(state=zeros)` differs from `predict(state=None)`**: Identical — both produce same hidden states.
3. **`add_sos=True` matches ONNX export**: ONNX export uses `add_sos=False` (set by `_prepare_for_export`).
4. **PowerShell `2>&1 | Out-Null` captures Python output**: Does not reliably — scripts must write to their own log files.
5. **`initialize_state()` returns encoder-derived state**: Returns zeros — not the cause of decode divergence.
6. **NeMo uses label-looping heuristics that can't be replicated**: False — the only behavioral difference was the state-update-on-blank rule, which is a simple fix.

### Key Technical Discoveries

1. ONNX decoder_joint output order is `[logits, prednet_lengths(int32!), output_states_1, output_states_2]` — states at indices 2,3 not 1,2
2. ONNX weights stored as external files; onnxruntime loads transparently
3. PyTorch ONNX export default opset = 17
4. Encoder layout in ONNX: `[B, D=1024, T]` (D in middle), joint internally transposes to `[B, T, D]`
5. Blank index = 1024 (last token, = vocab_size)
6. SentencePiece tokenizer with 1024 tokens, `model.tokenizer.tokenizer` is the inner sp model
7. **RNNT greedy decode state rule**: Do NOT update LSTM state on blank prediction — reuse the previous prediction across blank frames (matching NeMo's `GreedyBatchedRNNTLabelLoopingComputer` behavior)

### Precedent Projects

- `parakeet-rs` and `canary-rs` crates run sibling FastConformer-RNNT models in pure Rust via `ort` with CUDA/CoreML/DirectML execution providers
