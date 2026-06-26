# ARCHITECTURE.md

> Validated architecture for NemotronFlow. Every decision here is backed by spike evidence.

## Overview

NemotronFlow is a Tauri desktop application that provides push-to-talk speech-to-text using NVIDIA's Nemotron 3.5 ASR model, run entirely locally via ONNX Runtime.

## Architecture Decision: B (Pure Tauri + Rust)

Architecture A (Python sidecar + NeMo/PyTorch + IPC) was the original spec. This spike validated Architecture B (Pure Tauri + Rust `ort` crate) and it is the recommended approach.

**Why**: The ONNX export path works. The model exports to two ONNX subnets that load in onnxruntime. Architecture B eliminates the Python dependency entirely, reducing bundle size, startup time, and IPC complexity.

## Component Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    Tauri Desktop Shell                     │
│  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │  React Frontend   │  │  Rust Backend (ort crate)     │  │
│  │  - Overlay UI     │←→│  - Audio capture (cpal)      │  │
│  │  - Settings       │  │  - Feature extraction        │  │
│  │  - Transcription   │  │  - ONNX encoder inference    │  │
│  │    display         │  │  - RNNT greedy decoding       │  │
│  └──────────────────┘  │  - SentencePiece detokenize   │  │
│                         │  - Clipboard paste (arboard)  │  │
│                         └──────────────────────────────┘  │
│                                   │                        │
│                          ┌────────┴────────┐               │
│                          │  ONNX Runtime    │               │
│                          │  (CUDA EP)       │               │
│                          │  encoder.onnx    │               │
│                          │  decoder.onnx    │               │
│                          └─────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

## ONNX Pipeline (Validated)

### Inference Pipeline

```
Raw Audio (16kHz PCM)
    │
    ▼
Feature Extraction (Rust — port from NeMo preprocessor)
    ├── Resample to 16kHz if needed
    ├── Window: 25ms Hann, stride 10ms
    ├── FFT size: 512
    ├── Mel filterbank: 128 bands
    ├── Log energy
    └── Output: [B, 128, T] float32
    │
    ▼
ONNX Encoder (encoder-encoder.onnx)
    ├── Input: audio_signal [B, 128, T], length [B]
    ├── Output: encoded [B, 1024, T], encoded_lengths [B]
    └── ~2.4 GB model, 24 Conformer layers
    │
    ▼
RNNT Greedy Decode (Rust — custom implementation)
    ├── For each encoder frame t = 0..T:
    │   ├── Feed last_token + LSTM state → ONNX decoder_joint
    │   ├── Argmax logits [1025]
    │   ├── If blank → advance to next frame
    │   └── If non-blank → emit token, update state, repeat
    └── Output: list of token IDs
    │
    ▼
SentencePiece Detokenize (Rust — `sentencepiece` crate or `rust-sentencepiece`)
    ├── Vocab: 1024 tokens + blank (1024)
    └── Output: transcript string
```

### Streaming Pipeline (Future)

```
Audio Chunks (80ms–1120ms)
    │
    ▼
Feature Extraction (per chunk)
    │
    ▼
ONNX Streaming Encoder (encoder-encoder.onnx with cache)
    ├── Input: audio_signal [B, 128, T_chunk], length [B],
    │         cache_last_channel [B, 24, T, 1024],
    │         cache_last_time [B, 24, 1024, T],
    │         cache_last_channel_len [B]
    ├── Output: encoded [B, 1024, T_chunk],
    │          cache_last_channel_next, cache_last_time_next, ...
    └── Pass cache between chunks for context continuity
    │
    ▼
Incremental RNNT Decode
    └── Decode new frames, emit additional tokens
```

## Model Details

| Property | Value |
|----------|-------|
| Model | `nvidia/nemotron-speech-streaming-en-0.6b` |
| Architecture | Cache-Aware Streaming FastConformer-RNNT |
| Encoder layers | 24 Conformer layers |
| Encoder hidden | 1024 |
| Parameters | 618,084,865 |
| BPE vocab | 1024 tokens |
| Blank index | 1024 (last) |
| Mel bands | 128 |
| Sample rate | 16000 Hz |
| Window | 25ms Hann |
| Stride | 10ms |
| FFT | 512 |
| Chunk sizes | 80ms / 160ms / 560ms / 1120ms (att_context_size = [70, N]) |

## Rust Responsibilities

1. **Audio capture** via `cpal` — real-time microphone input
2. **Feature extraction** — mel spectrogram matching NeMo's preprocessor (128 bands, 25ms window, 10ms stride, log energy)
3. **ONNX inference** via `ort` crate with CUDA Execution Provider
4. **RNNT greedy decoding** — frame-looping with LSTM state management
5. **SentencePiece detokenization** — convert token IDs to text
6. **Clipboard injection** — paste transcript via `arboard` or platform API
7. **Tauri commands** — bridge frontend events to backend pipeline

## Python Responsibilities (Minimal — Training/Export Only)

1. **Model download** from HuggingFace
2. **ONNX export** via NeMo's `model.export(output="encoder.onnx")`
3. **No runtime dependency** — Python is not needed after export

## Tauri Responsibilities

1. **Desktop window management** — overlay window, always-on-top
2. **System tray** — hotkey registration, app lifecycle
3. **IPC** — between React frontend and Rust backend (Tauri commands)
4. **Settings persistence** — user preferences (language, hotkey, etc.)

## Audio Pipeline (Non-Streaming)

1. Capture 16kHz mono PCM from microphone via `cpal`
2. Accumulate audio until push-to-talk release (or silence timeout)
3. Apply VAD (Voice Activity Detection) to trim silence
4. Extract mel spectrogram features
5. Run ONNX encoder on full utterance
6. Run RNNT greedy decode
7. Detokenize and display/paste

## IPC Design

Tauri uses `invoke()` for frontend → backend calls. No JSON-RPC or WebSocket needed (unlike Architecture A). The Rust backend runs in the same process as the Tauri webview.

## Future Decisions

- **Streaming**: The streaming ONNX encoder is exported but not yet validated. Chunk-based inference requires managing cache tensors between chunks.
- **VAD**: Could use Silero VAD (also available as ONNX) or a simpler energy-based approach.
- **Feature extraction in Rust**: Need to port NeMo's `AudioToMelSpectrogramPreprocessor` to Rust. This is a straightforward port (windowing, FFT, mel filterbank, log).
- **SentencePiece in Rust**: The `sentencepiece` crate or `rust-sentencepiece` provides Rust bindings. The model's tokenizer is a standard SentencePiece model with 1024 tokens.
