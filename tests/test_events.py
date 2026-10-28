import copy
import json
from pathlib import Path

from opspilot.events import parse_event

EVENTS = Path(__file__).parent.parent / "samples" / "events"


def load(name: str) -> dict:
    return json.loads((EVENTS / name).read_text(encoding="utf-8"))


def test_oom_task_stop_reads_that_tasks_log_stream():
    [incident] = parse_event(load("ecs_task_stopped_oom.json"))
    assert incident.service == "orders-api"
    assert incident.region == "ap-south-1"
    assert incident.log_stream == "ecs/orders-api/3b9f0c2e7d4a4f1e9c8b6a5d4e3f2a1b"
    assert "exitCode=137, memory limit 256 MiB" in incident.trigger
    assert "OutOfMemoryError: Container killed due to memory usage" in incident.title
    assert incident.start < incident.end


def test_crash_with_exit_code_is_an_incident():
    [incident] = parse_event(load("ecs_task_stopped_missing_env.json"))
    assert "exit code 3" in incident.title


def test_health_check_stop_is_an_incident_even_with_exit_code_0():
    [incident] = parse_event(load("ecs_task_stopped_bad_health.json"))
    assert "health checks" in incident.title
    assert "Task failed ELB health checks" in incident.trigger


def test_deploy_replacement_and_manual_stop_are_ignored():
    event = load("ecs_task_stopped_oom.json")
    detail = event["detail"]
    detail.update(stopCode="ServiceSchedulerInitiated",
                  stoppedReason="Scaling activity initiated by (deployment ecs-svc/1234567890)")
    detail["containers"][0].update(exitCode=0)
    del detail["containers"][0]["reason"]
    assert parse_event(event) == []

    manual = load("ecs_task_stopped_oom.json")
    manual["detail"]["stopCode"] = "UserInitiated"
    assert parse_event(manual) == []


def test_same_failure_in_another_task_shares_the_dedup_key():
    first = load("ecs_task_stopped_oom.json")
    second = copy.deepcopy(first)
    second["detail"]["taskArn"] = second["detail"]["taskArn"][:-4] + "ffff"
    assert parse_event(first)[0].dedup_key == parse_event(second)[0].dedup_key


def test_alarm_delivered_by_sns():
    [incident] = parse_event(load("alarm_5xx_sns.json"))
    assert incident.region == "ap-south-1"
    assert incident.log_stream is None
    assert "HTTPCode_Target_5XX_Count" in incident.trigger
    assert (incident.end - incident.start).total_seconds() == 15 * 60


def test_alarm_returning_to_ok_is_ignored():
    event = load("alarm_5xx_sns.json")
    message = json.loads(event["Records"][0]["Sns"]["Message"])
    message["NewStateValue"] = "OK"
    event["Records"][0]["Sns"]["Message"] = json.dumps(message)
    assert parse_event(event) == []


def test_unrelated_events_are_ignored():
    assert parse_event({"Records": [{"Sns": {"Message": "plain text, not JSON"}}]}) == []
    assert parse_event({"Records": [{"Sns": {"Message": "42"}}]}) == []
    assert parse_event({"detail-type": "EC2 Instance State-change Notification", "detail": {}}) == []
