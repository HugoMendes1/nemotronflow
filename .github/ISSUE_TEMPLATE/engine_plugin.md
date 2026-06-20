---
name: ASR engine plugin
about: Request a new ASR engine backend
title: "[Engine] "
labels: enhancement, engine
---

**Engine name**
[e.g. WhisperASR, ParakeetASR, DeepgramASR]

**Provider**
[e.g. OpenAI, NVIDIA, Deepgram]

**Description**
Brief description of the ASR engine and why it should be supported.

**Key capabilities**
- Streaming / non-streaming
- Languages supported
- GPU requirements
- Approximate latency

**Implementation notes**
Any notes on how this engine would integrate with the `BaseASR` plugin
interface. See `backend/nemotronflow/asr/base.py` for the contract.
