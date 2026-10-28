from types import SimpleNamespace

from opspilot import slack
from opspilot.logs import console_url

INCIDENT = SimpleNamespace(title="ECS task stopped: orders-api (exit code 3)",
                           trigger="Stopped reason: Essential container in task exited")
DIAGNOSIS = {
    "affected_service": "orders-api",
    "root_cause": "Heap > container limit & <unbounded> cache.",
    "evidence": ['"exitCode": 137'],
    "severity": "high",
    "confidence": 0.9,
    "fix_steps": ["Raise memory", "Evict old entries"],
    "prevention": ["Alarm on memory"],
    "summary": "orders-api OOM-killed",
}


def texts(message: dict) -> str:
    parts = []
    for block in message["attachments"][0]["blocks"]:
        parts += [e["text"] for e in block["elements"]] if block["type"] == "context" else [block["text"]["text"]]
    return "\n".join(parts)


def test_colour_and_title_follow_severity():
    message = slack.build_message(INCIDENT, DIAGNOSIS)
    assert message["attachments"][0]["color"] == slack.SEVERITY_STYLE["high"][1]
    assert message["text"].startswith("🟠 HIGH: orders-api")
    assert "1. Raise memory\n2. Evict old entries" in texts(message)


def test_model_text_is_escaped_for_slack():
    body = texts(slack.build_message(INCIDENT, DIAGNOSIS))
    assert "Heap &gt; container limit &amp; &lt;unbounded&gt; cache." in body


def test_long_text_is_clipped_and_code_block_stays_closed():
    long = dict(DIAGNOSIS, root_cause="x" * 5000, evidence=["y" * 5000])
    for block in slack.build_message(INCIDENT, long)["attachments"][0]["blocks"]:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000
            if "Evidence" in block["text"]["text"]:
                assert block["text"]["text"].endswith("```")


def test_logs_link_and_failure_message():
    url = console_url("ap-south-1", "/ecs/orders-api", "ecs/orders-api/abc123")
    assert url.endswith("#logsV2:log-groups/log-group/$252Fecs$252Forders-api"
                        "/log-events/ecs$252Forders-api$252Fabc123")
    failure = slack.build_failure_message(INCIDENT, "model returned invalid JSON", url)
    body = texts(failure)
    assert "could not diagnose" in failure["text"]
    assert "Essential container in task exited" in body and url in body
