# CONTRIBUTING_AI.md

> Guide for future AI agents (GLM, Codex, Claude, GPT, Cursor, etc.) continuing development of NemotronFlow.

## Project Purpose

NemotronFlow is an open-source desktop application providing a premium Wispr Flow-style push-to-talk speech-to-text experience, powered by NVIDIA's Nemotron 3.5 ASR model (`nvidia/nemotron-speech-streaming-en-0.6b`). The goal is a lightweight, privacy-first, local-only ASR tool that runs entirely on the user's machine.

## Current Status

We are in the **Architecture Validation Spike** phase. The M0 scaffold is complete (in `NemotronFlow/`). The spike (in `spike/`) is validating whether the ASR model can run via ONNX in Rust without a Python dependency. Phase 2 (ONNX export + validation) is complete. Phase 3 (Rust ort validation) is next.

## Repository Structure

```
ZCodeProject/
├── NemotronFlow/          # Main project repo (the ONLY git repository)
│   ├── backend/           # Python backend (will be rewritten to Rust)
│   ├── frontend/          # React+TypeScript+Vite scaffold
│   ├── tests/             # Python tests
│   ├── docs/              # Project documentation (THIS DIRECTORY)
│   │   ├── PROJECT_STATE.md    # Current state — read this first
│   │   ├── SPIKE_REPORT.md     # Spike experiment history
│   │   ├── ARCHITECTURE.md     # Validated architecture
│   │   ├── DECISIONS.md        # Engineering decision log
│   │   ├── NEXT_STEPS.md       # Prioritized checklist
│   │   ├── CONTRIBUTING_AI.md  # This file
│   │   └── superpowers/        # Plans and specs (M0–M7)
│   ├── package.json       # pnpm workspace root
│   └── ...
├── spike/                  # Architecture validation spike (NOT part of NemotronFlow git repo)
│   ├── models/onnx/        # Exported ONNX files + weights (~2.4 GB)
│   ├── audio/              # Test audio samples
│   ├── scripts/            # All Python spike scripts
│   └── rust-validator/     # Rust ort validation (empty, next phase)
```

## Files That Must Never Be Rewritten

- `NemotronFlow/` M0 scaffold artifacts (committed, tagged `v0.1.0-m0`) — do not modify unless on a new branch.
- `spike/models/nemotron-speech-streaming-en-0.6b.nemo` — the 2.4 GB model file.
- `spike/models/onnx/` — exported ONNX files. Regenerating requires running the export scripts (~30 min each on CPU).

## How to Continue Development

1. **Read `docs/PROJECT_STATE.md` first** — it has the current state, blockers, and next steps.
2. **Read `docs/NEXT_STEPS.md`** — it has the prioritized checklist.
3. **Read `docs/DECISIONS.md`** before making any architectural decision.
4. **Read `docs/SPIKE_REPORT.md`** before repeating any investigation — all discoveries are recorded there.

## Known Technical Discoveries (Do Not Reinvestigate)

1. **`torch.cuda.is_available()` must be called before `import nemo` on Windows** — otherwise segfault. This is a NeMo/Windows/CUDA compatibility issue.
2. **numpy must be < 2.0** — numpy 2.x causes NeMo to segfault during import.
3. **Model GPU load segfaults on Windows** — use `CUDA_VISIBLE_DEVICES=""` and `map_location="cpu"`.
4. **ONNX decoder_joint returns raw logits** (not log_softmax). PyTorch applies log_softmax on CPU. Argmax is identical.
5. **ONNX decoder_joint output indices**: `[0]=logits`, `[1]=prednet_lengths` (int32!), `[2]=output_states_1`, `[3]=output_states_2`. The states are at indices 2 and 3, not 1 and 2.
6. **ONNX encoder layout**: `[B, D=1024, T]` (hidden dim in middle position).
7. **Blank token index**: 1024 (last, equals vocab_size).
8. **SentencePiece vocab**: 1024 tokens. Model at `model.tokenizer.tokenizer`.

## Coding Standards

- **Rust**: Follow standard `cargo fmt` and `cargo clippy` conventions. Use `ort` crate with `load-dynamic` feature for CUDA support.
- **Python**: Use `ruff` for linting, `mypy` for types. Python is only used for model export — not runtime.
- **Shell**: Spike scripts use Git Bash (MSYS2) on Windows. Use `"/c/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe"` as the Python path. Scripts must write output to log files (stdout may be swallowed by shell redirection).
- **Documentation**: Update `docs/PROJECT_STATE.md`, `docs/NEXT_STEPS.md`, `docs/SPIKE_REPORT.md`, and `docs/DECISIONS.md` at the end of every coding session.

## Spike System

The spike runs in `spike/`, separate from the `NemotronFlow/` repo. It validates technical assumptions before committing to architecture changes.

### Running Spike Scripts

```bash
# Python path
PYTHON="/c/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe"

# All scripts require CUDA_VISIBLE_DEVICES="" and torch.cuda.is_available() before nemo import.
# Scripts write to log files because PowerShell/Git Bash may not capture stdout.

# Example: run export script
"$PYTHON" -u "C:\Users\Administrator\ZCodeProject\spike\scripts\04e_export_nonstreaming.py" > /tmp/out.txt 2>&1
# Then read the log file:
cat "C:\Users\Administrator\ZCodeProject\spike\export_log.txt"
```

### ONNX Model Files

- Non-streaming: `spike/models/onnx/encoder-encoder.onnx` + `decoder_joint-encoder.onnx`
- Streaming: `spike/models/onnx/streaming/encoder-encoder.onnx` + `decoder_joint-encoder.onnx`
- Weights are external files in the same directory (~2.4 GB total)
- onnxruntime loads them transparently

## How to Update PROJECT_STATE.md

After every coding session:

1. Update "Current Spike Phase" to reflect progress
2. Update "Current Blocker" if changed
3. Update "Last Successful Validation" with new results
4. Update "Important Scripts" if new scripts were created
5. Update "Known Technical Discoveries" with any new findings
6. Update progress percentages

## How to Write Commits

Follow conventional commits format:

```
type(scope): description

Examples:
  spike(onnx): validate encoder export matches PyTorch within 1e-4
  spike(rust): load encoder.onnx via ort crate with CPU EP
  feat(core): implement mel spectrogram feature extraction
  docs: update PROJECT_STATE.md after Phase 3 validation
```

## Current Roadmap

1. ✅ M0: Scaffold NemotronFlow repo
2. ✅ Typr evaluation (decided not to pivot)
3. 🔄 Architecture validation spike (Phase 2 complete, Phase 3 next)
4. ⏳ Rewrite NemotronFlow backend in Rust (after spike validates Architecture B)
5. ⏳ Implement audio capture, feature extraction, inference pipeline
6. ⏳ Implement streaming (chunk-by-chunk) inference
7. ⏳ Polish UI, settings, hotkeys, system tray

## Environment Quick Reference

| Item | Value |
|------|-------|
| OS | Windows 10, Build 26200 (x64) |
| GPU | NVIDIA RTX 3090 Ti, 24 GB, Driver 596.36 |
| CUDA | 13.2 (runtime only, no toolkit) |
| Python | 3.11.0 at `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\` |
| Rust | 1.96.0 at `C:\Users\Administrator\.cargo\bin\` |
| PyTorch | 2.7.0+cu131 |
| NeMo | 2.7.3 |
| numpy | 1.26.4 |
| Model path | `C:\Users\Administrator\ZCodeProject\spike\models\nemotron-speech-streaming-en-0.6b.nemo` |
| ONNX (non-streaming) | `C:\Users\Administrator\ZCodeProject\spike\models\onnx\` |
| ONNX (streaming) | `C:\Users\Administrator\ZCodeProject\spike\models\onnx\streaming\` |
