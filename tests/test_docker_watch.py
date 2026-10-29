from types import SimpleNamespace

import pytest

from opspilot import docker_watch, pipeline

DIAGNOSIS = {
    "affected_service": "orders-api", "root_cause": "Unbounded cache.", "evidence": ["x"],
    "severity": "high", "confidence": 0.9, "fix_steps": ["a"], "prevention": ["b"],
    "summary": "orders-api OOM-killed",
}
CONTAINER_ID = "abc123def4567890"


def attrs(**state) -> dict:
    return {
        "Id": CONTAINER_ID,
        "Name": "/orders-api",
        "RestartCount": 0,
        "State": {"ExitCode": 0, "OOMKilled": False, "Error": "",
                  "StartedAt": "2026-10-29T10:00:00.1Z", "FinishedAt": "2026-10-29T10:00:12.9Z", **state},
        "Config": {"Image": "orders-api", "Healthcheck": {"Test": ["CMD-SHELL", "python -c check_health"]}},
        "HostConfig": {"Memory": 256 * 2**20},
    }


class FakeContainer:
    def __init__(self, container_attrs: dict, logs: bytes = b'{"message": "Cached order history batch"}\n'):
        self.attrs, self._logs = container_attrs, logs

    def logs(self, **kwargs) -> bytes:
        return self._logs


def make_watcher(container: FakeContainer):
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda cid: container, list=lambda **kw: [container]))
    posted = []
    return docker_watch.Watcher(client, posted.append), posted


def event(action: str) -> dict:
    return {"Type": "container", "Action": action, "Actor": {"ID": CONTAINER_ID, "Attributes": {}}}


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    pipeline._recently_diagnosed.clear()
    seen = []
    monkeypatch.setattr(pipeline, "diagnose", lambda text: seen.append(text) or DIAGNOSIS)
    return seen


def test_oom_kill_incident():
    incident = docker_watch.died_incident(attrs(ExitCode=137, OOMKilled=True))
    assert incident.title == "Container died: orders-api (OOM-killed)"
    for line in ("Exit code: 137", "OOMKilled: true", "Memory limit: 256 MiB"):
        assert line in incident.trigger


def test_crash_and_clean_exit():
    assert "exit code 3" in docker_watch.died_incident(attrs(ExitCode=3)).title
    assert docker_watch.died_incident(attrs(ExitCode=0)) is None


def test_unhealthy_incident_includes_the_failing_check():
    health = {"Status": "unhealthy", "FailingStreak": 2,
              "Log": [{"ExitCode": 1, "Output": "urllib.error.HTTPError: HTTP Error 500: Internal Server Error\n"}]}
    incident = docker_watch.unhealthy_incident(attrs(Health=health))
    for text in ("HTTP Error 500", "python -c check_health", "Failing streak: 2"):
        assert text in incident.trigger


def test_count_error_lines():
    logs = ('{"level": "INFO", "message": "GET /orders 200"}\n'
            '{"level": "ERROR", "message": "GET /orders 500"}\n'
            "plain ERROR line\n"
            "Traceback (most recent call last):\n"
            '  File "app.py", line 3\n')
    assert docker_watch.count_error_lines(logs) == 3


def test_oom_die_event_is_diagnosed_and_posted_once(fake_model):
    watcher, posted = make_watcher(FakeContainer(attrs(ExitCode=137, OOMKilled=True)))
    watcher.on_event(event("die"))
    watcher.on_event(event("die"))  # the same container dying again: no second alert
    assert len(posted) == 1
    assert "OOMKilled: true" in fake_model[0] and "Cached order history batch" in fake_model[0]


def test_docker_stop_is_ignored():
    watcher, posted = make_watcher(FakeContainer(attrs(ExitCode=137)))
    watcher.on_event(event("kill"))
    watcher.on_event(event("die"))
    assert posted == []


def test_oom_kill_is_reported_even_right_after_a_kill_event():
    watcher, posted = make_watcher(FakeContainer(attrs(ExitCode=137, OOMKilled=True)))
    watcher.on_event(event("kill"))
    watcher.on_event(event("die"))
    assert len(posted) == 1


def test_unhealthy_event_is_posted_and_other_events_ignored():
    health = {"FailingStreak": 2, "Log": [{"ExitCode": 1, "Output": "HTTP Error 500"}]}
    watcher, posted = make_watcher(FakeContainer(attrs(Health=health)))
    watcher.on_event(event("start"))
    watcher.on_event(event("health_status: healthy"))
    assert posted == []
    watcher.on_event(event("health_status: unhealthy"))
    assert len(posted) == 1 and "unhealthy" in posted[0]["attachments"][0]["blocks"][2]["elements"][0]["text"]
