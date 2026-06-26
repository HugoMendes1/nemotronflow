# NEXT_STEPS.md

> Prioritized checklist. Keep short and actionable.

## Completed

- [x] M0: Project scaffold (NemotronFlow repo, 5 commits, CI, lint, tests)
- [x] Typr evaluation (16% coverage, not worth pivoting; 4 techniques cherry-pickable)
- [x] Spike Phase 0: Install prerequisites
- [x] Spike Phase 1: Download model from HuggingFace
- [x] Spike Phase 2a: Export non-streaming ONNX (encoder + decoder_joint)
- [x] Spike Phase 2b: Export streaming ONNX (with cache tensors)
- [x] Spike Phase 2c-1: Prove ONNX encoder correctness (max diff 6e-6)
- [x] Spike Phase 2c-2: Prove ONNX decoder_joint correctness (argmax matches PyTorch)
- [x] Spike Phase 2c-3: Full ONNX pipeline produces transcriptions (~90% word match)

## In Progress

- [ ] (None currently)

## Next (Immediate)

- [ ] **Spike Phase 3**: Create Rust project, load ONNX models via `ort` crate, run inference on test audio
  - Init `spike/rust-validator/` with Cargo.toml
  - Add `ort` dependency with `load-dynamic` feature
  - Load `encoder-encoder.onnx` and `decoder_joint-encoder.onnx`
  - Replicate the mel feature extraction (or use pre-computed features)
  - Run encoder → decoder_joint → greedy decode → detokenize
  - Compare transcript against reference

## Future

- [ ] **Spike Phase 4**: Benchmark startup time, inference latency, memory (CPU + CUDA EP)
- [ ] **Spike Phase 5**: Evaluate streaming ONNX (chunk-by-chunk with cache tensors)
- [ ] **Spike Phase 6**: Produce final A/B architecture recommendation report
- [ ] Port mel spectrogram preprocessor to Rust
- [ ] Extract SentencePiece model from .nemo for Rust loading
- [ ] Implement VAD (Voice Activity Detection)
- [ ] Update NemotronFlow spec to Architecture B
- [ ] Rewrite NemotronFlow backend in Rust
