"""slack_qa tests: put slack_qa/ on sys.path (the service runs as `cd slack_qa && uvicorn app:app`)
and give every test a fresh STATE_DIR plus a known Slack signing secret."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-bot-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    for k in ("QA_ENABLED", "QA_DEFAULT_MUTED", "QA_DAILY_USD", "QA_SCAN_THRESHOLD", "QA_CHANNEL_HOURLY"):
        monkeypatch.delenv(k, raising=False)
