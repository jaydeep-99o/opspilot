"""Read the log lines around an incident from CloudWatch Logs."""

from datetime import datetime
from urllib.parse import quote

MAX_EVENTS = 2000


def fetch_logs(client, log_group: str, start: datetime, end: datetime,
               log_stream: str | None = None) -> str:
    """Return the messages logged between start and end, oldest first, one per line.

    `client` is a boto3 CloudWatch Logs client (anything with filter_log_events works). When
    a task's own stream is given but has nothing (for example, the task died before logging),
    the whole log group is read instead.
    """
    params = {"logGroupName": log_group,
              "startTime": int(start.timestamp() * 1000), "endTime": int(end.timestamp() * 1000)}
    events = []
    if log_stream:
        try:
            events = _filter(client, {**params, "logStreamNames": [log_stream]})
        except Exception:  # e.g. the stream doesn't exist; fall back to the whole group
            events = []
    if not events:
        events = _filter(client, params)
    events.sort(key=lambda event: event["timestamp"])
    return "\n".join(event["message"].rstrip("\n") for event in events[-MAX_EVENTS:])


def _filter(client, params: dict) -> list[dict]:
    events, token = [], None
    while True:
        page = client.filter_log_events(**params, **({"nextToken": token} if token else {}))
        events += page.get("events", [])
        token = page.get("nextToken")
        if not token or len(events) >= 10 * MAX_EVENTS:
            return events


def console_url(region: str, log_group: str, log_stream: str | None = None) -> str:
    """Link to the logs in the CloudWatch console, which uses its own "$25"-escaped encoding."""
    def encode(value: str) -> str:
        return quote(value, safe="").replace("%", "$25")

    url = (f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
           f"#logsV2:log-groups/log-group/{encode(log_group)}")
    if log_stream:
        url += f"/log-events/{encode(log_stream)}"
    return url
