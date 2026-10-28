"""AWS Lambda entry point: AWS event -> recent logs -> Nemotron diagnosis -> Slack.

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
import time

import openai

from opspilot import slack
from opspilot.diagnose import DiagnosisError, diagnose
from opspilot.events import Incident, parse_event
from opspilot.logs import console_url, fetch_logs

log = logging.getLogger("opspilot")
log.setLevel(logging.INFO)

LOG_CHAR_BUDGET = 10_000  # leaves room for the trigger details within diagnose()'s input limit
DEDUP_SECONDS = 600

# Failures diagnosed recently. It survives between calls while the Lambda stays warm, so a
# crash loop (a new task failing every minute) is posted once instead of every time.
_recently_diagnosed: dict[str, float] = {}


def lambda_handler(event: dict, context) -> list[dict]:
    import boto3  # provided by the Lambda runtime; imported here so local runs don't need it

    _load_secrets()
    return handle(event, boto3.client("logs"),
                  post=lambda message: slack.post(os.environ["SLACK_WEBHOOK_URL"], message))


def handle(event: dict, logs_client, post) -> list[dict]:
    """Diagnose every incident in the event and hand each Slack message to `post`."""
    results = []
    for incident in parse_event(event, os.environ.get("LOG_STREAM_PREFIX", "ecs")):
        if _seen_recently(incident.dedup_key):
            log.info("Skipping repeat of %s", incident.dedup_key)
            results.append({"incident": incident.title, "status": "duplicate"})
        else:
            results.append(_diagnose_and_post(incident, logs_client, post))
    return results


def _diagnose_and_post(incident: Incident, logs_client, post) -> dict:
    log_group = os.environ.get("LOG_GROUP") or (f"/ecs/{incident.service}" if incident.service else None)
    logs, url = "", None
    if log_group:
        logs = fetch_logs(logs_client, log_group, incident.start, incident.end, incident.log_stream)
        if incident.region:
            url = console_url(incident.region, log_group, incident.log_stream)
    # The trigger goes first: the stop reason and exit code are often the best evidence.
    text = (f"=== What AWS reported ===\n{incident.trigger}\n\n"
            f"=== CloudWatch Logs: {log_group or 'none'} ===\n{_tail(logs, LOG_CHAR_BUDGET)}")
    try:
        diagnosis = diagnose(text)
    except (DiagnosisError, openai.APIError) as err:
        log.exception("Diagnosis failed for %s", incident.title)
        post(slack.build_failure_message(incident, str(err), url))
        return {"incident": incident.title, "status": "diagnosis_failed", "error": str(err)}
    post(slack.build_message(incident, diagnosis, url))
    log.info("Posted %s diagnosis for %s", diagnosis["severity"], incident.title)
    return {"incident": incident.title, "status": "posted",
            "severity": diagnosis["severity"], "summary": diagnosis["summary"]}


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


def _seen_recently(key: str) -> bool:
    now = time.time()
    if _recently_diagnosed.get(key, 0) > now:
        return True
    _recently_diagnosed[key] = now + DEDUP_SECONDS
    return False


def _tail(text: str, limit: int) -> str:
    """Keep the most recent lines that fit in `limit` characters."""
    if len(text) <= limit:
        return text
    kept = text[-limit:]
    return "[... earlier log lines omitted ...]\n" + kept[kept.find("\n") + 1:]
