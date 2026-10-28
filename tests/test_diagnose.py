"""diagnose() with a fake model client: no network, no API key, no cost."""

import json
from types import SimpleNamespace

import pytest

from opspilot import diagnose as mod

LOGS = """2026-10-26T14:31:47Z  WARN  Loaded 184,302 invoices into memory
  "exitCode": 137,
  "reason": "OutOfMemoryError: Container killed due to memory usage"
"""
GOOD = {
    "affected_service": "order-processor",
    "root_cause": "JVM heap -Xmx1536m exceeds the 1024 MiB task memory.",
    "evidence": ['"exitCode": 137,', "Loaded 184,302   invoices into memory"],  # extra spaces
    "severity": "High",
    "confidence": 0.9,
    "fix_steps": ["Lower -Xmx", "Paginate the export"],
    "prevention": ["Alarm on memory utilization"],
    "summary": "order-processor OOM-killed\n(exit 137)",
}


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.replies.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("NEBIUS_MODEL", "nvidia/default-model")

    def install(*replies):
        fake = FakeClient(replies)
        monkeypatch.setattr(mod, "get_client", lambda: fake)
        return fake
    return install


def test_reasoning_fences_and_normalization(model):
    fake = model("<think>exit 137 means OOM</think>\nHere you go:\n```json\n" + json.dumps(GOOD) + "\n```")
    result = mod.diagnose(LOGS)
    assert len(fake.calls) == 1 and fake.calls[0]["model"] == "nvidia/default-model"
    assert result["severity"] == "high"
    assert result["summary"] == "order-processor OOM-killed (exit 137)"
    assert len(result["evidence"]) == 2
    assert list(result) == ["affected_service", "root_cause", "evidence", "severity", "confidence",
                            "fix_steps", "prevention", "summary"]


def test_model_override(model):
    fake = model(json.dumps(GOOD))
    mod.diagnose(LOGS, model="nvidia/other")
    assert fake.calls[0]["model"] == "nvidia/other"


def test_invented_evidence_is_dropped(model):
    model(json.dumps(dict(GOOD, evidence=['"exitCode": 137,', "ERROR database connection refused"])))
    assert mod.diagnose(LOGS)["evidence"] == ['"exitCode": 137,']


def test_all_evidence_invented_triggers_a_retry(model):
    fake = model(json.dumps(dict(GOOD, evidence=["ERROR database connection refused"])), json.dumps(GOOD))
    mod.diagnose(LOGS)
    assert len(fake.calls) == 2 and "evidence" in fake.calls[1]["messages"][-1]["content"]


def test_retry_lists_every_problem(model):
    bad = {k: v for k, v in GOOD.items() if k != "prevention"} | {"severity": "urgent", "confidence": 1.7}
    fake = model(json.dumps(bad), json.dumps(GOOD))
    mod.diagnose(LOGS)
    retry = fake.calls[1]["messages"][-1]["content"]
    assert all(key in retry for key in ('"severity"', '"confidence"', '"prevention"'))


def test_gives_up_after_two_invalid_answers(model):
    fake = model('{"affected_service": "x", ', "sorry, I cannot help")
    with pytest.raises(mod.DiagnosisError):
        mod.diagnose(LOGS)
    assert len(fake.calls) == 2


def test_empty_answer_is_retried(model):
    fake = model(None, json.dumps(GOOD))
    mod.diagnose(LOGS)
    assert len(fake.calls) == 2


def test_input_checks():
    with pytest.raises(ValueError):
        mod._validate(dict(GOOD, confidence=True), LOGS)
    with pytest.raises(ValueError):
        mod.diagnose("   ")
    message = mod._build_user_message("A" * 20_000 + "FINAL_ERROR")
    assert "FINAL_ERROR" in message and "truncated" in message
