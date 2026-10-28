"""Turn AWS events into incidents for OpsPilot to diagnose.

Two kinds of trigger are understood, delivered directly or wrapped in SNS:
- a CloudWatch alarm entering the ALARM state (for example, too many 5xx responses)
- an EventBridge "ECS Task State Change" event for a task that stopped unexpectedly
Anything else (an alarm going back to OK, tasks replaced by a deploy) is ignored.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

LOOKBACK = timedelta(minutes=15)


@dataclass
class Incident:
    title: str              # one line for humans, e.g. "ECS task stopped: orders-api (exit code 3)"
    trigger: str            # what AWS reported, as text for the model
    region: str
    service: str | None     # the ECS service, when the event names one
    start: datetime         # time window of logs to read
    end: datetime
    log_stream: str | None  # one task's log stream, or None to read the whole log group
    dedup_key: str          # the same failure repeating gets the same key


def parse_event(event: dict, stream_prefix: str = "ecs") -> list[Incident]:
    """Return the incidents in a Lambda event (one SNS delivery can carry several).

    `stream_prefix` is the awslogs-stream-prefix of the ECS task definition, used to find
    the stopped task's own log stream.
    """
    if "Records" in event:
        incidents = []
        for record in event["Records"]:
            try:
                message = json.loads(record["Sns"]["Message"])
            except (KeyError, json.JSONDecodeError):
                continue  # not an SNS message we understand
            if isinstance(message, dict):
                incidents += parse_event(message, stream_prefix)
        return incidents
    if "AlarmName" in event:
        return [_from_alarm(event)] if event.get("NewStateValue") == "ALARM" else []
    if event.get("detail-type") == "ECS Task State Change":
        incident = _from_ecs_task(event, stream_prefix)
        return [incident] if incident else []
    return []


def _from_alarm(alarm: dict) -> Incident:
    metric = alarm.get("Trigger", {})
    dimensions = ", ".join(f"{d['name']}={d['value']}" for d in metric.get("Dimensions", []))
    changed_at = datetime.fromisoformat(alarm["StateChangeTime"])
    lines = [
        f"CloudWatch alarm {alarm['AlarmName']} went from {alarm.get('OldStateValue')} to ALARM "
        f"at {changed_at.isoformat()}",
        f"Reason: {alarm.get('NewStateReason', '')}",
        f"Metric: {metric.get('Namespace')} {metric.get('MetricName')} ({dimensions})",
    ]
    if alarm.get("AlarmDescription"):
        lines.append(f"Description: {alarm['AlarmDescription']}")
    arn = alarm.get("AlarmArn", "")  # arn:aws:cloudwatch:<region>:<account>:alarm:<name>
    return Incident(
        title=f"CloudWatch alarm: {alarm['AlarmName']}",
        trigger="\n".join(lines),
        region=arn.split(":")[3] if arn.count(":") >= 5 else "",
        service=None,
        start=changed_at - LOOKBACK,
        end=changed_at,
        log_stream=None,
        dedup_key=f"alarm:{alarm['AlarmName']}",
    )


def _from_ecs_task(event: dict, stream_prefix: str) -> Incident | None:
    task = event["detail"]
    if task.get("lastStatus") != "STOPPED" or task.get("stopCode") == "UserInitiated":
        return None
    reason = task.get("stoppedReason", "")
    containers = task.get("containers", [])
    crashed = [c for c in containers if c.get("exitCode") not in (None, 0)]
    failed_health_check = "health check" in reason.lower()
    if not crashed and not failed_health_check:
        return None  # stopped on purpose, e.g. replaced by a deployment or scaled in

    cluster = task["clusterArn"].split("/")[-1]
    task_id = task["taskArn"].split("/")[-1]
    group = task.get("group", "")
    service = group.removeprefix("service:") if group.startswith("service:") else None
    stopped_at = datetime.fromisoformat(task["stoppedAt"])
    started_at = datetime.fromisoformat(task["startedAt"]) if task.get("startedAt") else None

    lines = [
        f"ECS task {task_id} stopped (cluster {cluster}, {group or 'no service'})",
        f"Task definition: {task.get('taskDefinitionArn', '').split('/')[-1]}",
        f"Task size: cpu {task.get('cpu')} units, memory {task.get('memory')} MiB",
        f"Stop code: {task.get('stopCode')}",
        f"Stopped reason: {reason}",
    ]
    for container in containers:
        line = f"Container {container.get('name')}: exitCode={container.get('exitCode')}"
        if container.get("memory"):
            line += f", memory limit {container['memory']} MiB"
        if container.get("reason"):
            line += f", reason: {container['reason']}"
        lines.append(line)
    lines.append(f"Started at {started_at.isoformat() if started_at else 'never'}, "
                 f"stopped at {stopped_at.isoformat()}")

    if crashed:
        first = crashed[0]
        what = first.get("reason") or f"exit code {first.get('exitCode')}"
        signature = ",".join(str(c.get("exitCode")) for c in crashed)
    else:
        what, signature = "failed load balancer health checks", "health-check"
    container_name = (crashed or containers or [{}])[0].get("name")
    start = stopped_at - LOOKBACK
    if started_at and started_at > start:
        start = started_at
    return Incident(
        title=f"ECS task stopped: {service or group or cluster} ({what})",
        trigger="\n".join(lines),
        region=event.get("region", ""),
        service=service,
        start=start,
        end=stopped_at + timedelta(minutes=1),  # the last lines can arrive just after the stop
        log_stream=f"{stream_prefix}/{container_name}/{task_id}" if container_name else None,
        dedup_key=f"ecs:{cluster}:{group}:{task.get('stopCode')}:{signature}",
    )
