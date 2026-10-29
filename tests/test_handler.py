import json
from pathlib import Path

import pytest

from opspilot import handler, pipeline
from opspilot.diagnose import DiagnosisError

EVENTS = Path(__file__).parent.parent / "samples" / "events"
DIAGNOSIS = {
    "affected_service": "orders-api", "root_cause": "Unbounded cache.", "evidence": ["x"],
    "severity": "high", "confidence": 0.9, "fix_steps": ["a"], "prevention": ["b"],
    "summary": "orders-api OOM-killed",
}


class FakeLogs:
    def __init__(self, lines, stream_lines=None):
        self.lines, self.stream_lines, self.calls = lines, stream_lines, []

    def filter_log_events(self, **params):
        self.calls.append(params)
        lines = self.stream_lines if "logStreamNames" in params and self.stream_lines is not None else self.lines
        return {"events": [{"timestamp": n, "message": line} for n, line in enumerate(lines)]}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    pipeline._recently_diagnosed.clear()
    monkeypatch.delenv("LOG_GROUP", raising=False)


def event(name: str) -> dict:
    return json.loads((EVENTS / name).read_text(encoding="utf-8"))


def test_diagnoses_with_trigger_and_logs_then_posts(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "diagnose", lambda text: seen.append(text) or DIAGNOSIS)
    posted, logs = [], FakeLogs(['{"message": "Cached order history batch", "rss_mb": 249}'])

    [result] = handler.handle(event("ecs_task_stopped_oom.json"), logs, posted.append)

    assert result["status"] == "posted" and result["severity"] == "high"
    assert logs.calls[0]["logGroupName"] == "/ecs/orders-api"
    assert logs.calls[0]["logStreamNames"] == ["ecs/orders-api/3b9f0c2e7d4a4f1e9c8b6a5d4e3f2a1b"]
    assert seen[0].index("OutOfMemoryError") < seen[0].index("Cached order history batch")
    assert posted[0]["text"].startswith("🟠 HIGH")
    assert "console.aws.amazon.com" in json.dumps(posted[0])


def test_crash_loop_is_posted_once(monkeypatch):
    monkeypatch.setattr(pipeline, "diagnose", lambda text: DIAGNOSIS)
    posted = []
    handler.handle(event("ecs_task_stopped_oom.json"), FakeLogs(["line"]), posted.append)
    [repeat] = handler.handle(event("ecs_task_stopped_oom.json"), FakeLogs(["line"]), posted.append)
    assert repeat["status"] == "duplicate" and len(posted) == 1


def test_failed_diagnosis_still_posts_what_aws_reported(monkeypatch):
    def broken(text):
        raise DiagnosisError("invalid response after 2 attempts")

    monkeypatch.setattr(pipeline, "diagnose", broken)
    posted = []
    [result] = handler.handle(event("ecs_task_stopped_missing_env.json"), FakeLogs(["line"]), posted.append)
    assert result["status"] == "diagnosis_failed"
    assert "could not diagnose" in posted[0]["text"]


def test_reads_whole_group_when_task_stream_is_empty(monkeypatch):
    monkeypatch.setattr(pipeline, "diagnose", lambda text: DIAGNOSIS)
    logs = FakeLogs(["from the group"], stream_lines=[])
    handler.handle(event("ecs_task_stopped_bad_health.json"), logs, lambda message: None)
    assert "logStreamNames" in logs.calls[0] and "logStreamNames" not in logs.calls[1]


def test_alarm_uses_configured_log_group_and_keeps_newest_logs(monkeypatch):
    monkeypatch.setenv("LOG_GROUP", "/ecs/orders-api")
    seen = []
    monkeypatch.setattr(pipeline, "diagnose", lambda text: seen.append(text) or DIAGNOSIS)
    lines = [f"old line {n:05d} " + "." * 80 for n in range(400)] + ["NEWEST LINE"]
    handler.handle(event("alarm_5xx_sns.json"), FakeLogs(lines), lambda message: None)
    assert "NEWEST LINE" in seen[0] and "old line 00000" not in seen[0]
    assert "earlier log lines omitted" in seen[0] and "HTTPCode_Target_5XX_Count" in seen[0]
