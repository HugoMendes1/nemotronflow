from __future__ import annotations

from pathlib import Path

import pytest

from nemotronflow import logging as nf_logging


@pytest.fixture(autouse=True)
def _isolated_logging() -> None:
    nf_logging.reset_session(session_id="sess-test")
    nf_logging.configure(level="DEBUG", log_dir=None)


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("NEMOTRONFLOW_DATA_DIR", str(data))
    return data
