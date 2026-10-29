"""AWS Lambda entry point: AWS event -> recent CloudWatch logs -> Nemotron diagnosis -> Slack.

Lambda handler setting: opspilot.handler.lambda_handler

Environment variables:
    NEBIUS_BASE_URL, NEBIUS_MODEL  as for diagnose()
    NEBIUS_API_KEY_PARAM           SSM SecureString name holding the Nebius key
    SLACK_WEBHOOK_URL_PARAM        SSM SecureString name holding the Slack webhook URL
                                   (NEBIUS_API_KEY / SLACK_WEBHOOK_URL can be set directly instead)
    LOG_GROUP                      log group to read (default: /ecs/<service from the event>)
    LOG_STREAM_PREFIX              awslogs-stream-prefix of the ECS task definition (default: ecs)
"""

import logging
import os

from opspilot import slack
from opspilot.events import Incident, parse_event
from opspilot.logs import console_url, fetch_logs
from opspilot.pipeline import report, seen_recently

log = logging.getLogger("opspilot")


def lambda_handler(event: dict, context) -> list[dict]:
    import boto3  # provided by the Lambda runtime; imported here so local runs don't need it

    _load_secrets()
    return handle(event, boto3.client("logs"),
                  post=lambda message: slack.post(os.environ["SLACK_WEBHOOK_URL"], message))


def handle(event: dict, logs_client, post) -> list[dict]:
    """Diagnose every incident in the event and hand each Slack message to `post`."""
    results = []
    for incident in parse_event(event, os.environ.get("LOG_STREAM_PREFIX", "ecs")):
        if seen_recently(incident.dedup_key):
            log.info("Skipping repeat of %s", incident.dedup_key)
            results.append({"incident": incident.title, "status": "duplicate"})
        else:
            results.append(_fetch_logs_and_report(incident, logs_client, post))
    return results


def _fetch_logs_and_report(incident: Incident, logs_client, post) -> dict:
    log_group = os.environ.get("LOG_GROUP") or (f"/ecs/{incident.service}" if incident.service else None)
    logs, url = "", None
    if log_group:
        logs = fetch_logs(logs_client, log_group, incident.start, incident.end, incident.log_stream)
        if incident.region:
            url = console_url(incident.region, log_group, incident.log_stream)
    return report(incident, logs, f"CloudWatch {log_group or '(none)'}", post, url)


def _load_secrets() -> None:
    """Copy secrets from SSM Parameter Store into the environment (once per cold start)."""
    missing = [name for name in ("NEBIUS_API_KEY", "SLACK_WEBHOOK_URL")
               if not os.environ.get(name) and os.environ.get(f"{name}_PARAM")]
    if not missing:
        return
    import boto3

    ssm = boto3.client("ssm")
    for name in missing:
        parameter = ssm.get_parameter(Name=os.environ[f"{name}_PARAM"], WithDecryption=True)
        os.environ[name] = parameter["Parameter"]["Value"]
