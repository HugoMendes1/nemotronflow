from __future__ import annotations

import json

from nemotronflow.logging import get_logger, reset_session


def test_logger_emits_jsonl_with_session_and_utterance_ids() -> None:
    reset_session(session_id="sess-abc")
    log = get_logger("test")
    log.info("transcribe_done", utterance_id="utt-1", inference_ms=287)
    record = get_logger.last_record()
    assert record is not None
    assert record["event"] == "transcribe_done"
    assert record["session_id"] == "sess-abc"
    assert record["utterance_id"] == "utt-1"
    assert record["inference_ms"] == 287  # noqa: PLR2004
    # JSONL contract: the record must be JSON-serializable.
    json.loads(json.dumps(record))
