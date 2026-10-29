"""What every trigger shares: diagnose an incident, post it to Slack, and skip repeats.

Both the AWS Lambda (handler.py) and the local Docker watcher (docker_watch.py) use this.
"""

import logging
import time

import openai

from opspilot import slack
from opspilot.diagnose import DiagnosisError, diagnose
from opspilot.events import Incident

log = logging.getLogger("opspilot")
log.setLevel(logging.INFO)

LOG_CHAR_BUDGET = 10_000  # leaves room for the trigger details within diagnose()'s input limit
DEDUP_SECONDS = 600

# Failures diagnosed recently. A crash loop (the same failure every minute) is posted once.
_recently_diagnosed: dict[str, float] = {}


def seen_recently(key: str) -> bool:
    """True if this failure was already reported in the last DEDUP_SECONDS (and marks it seen)."""
    now = time.time()
    if _recently_diagnosed.get(key, 0) > now:
        return True
    _recently_diagnosed[key] = now + DEDUP_SECONDS
    return False


def report(incident: Incident, logs: str, logs_source: str, post, logs_url: str | None = None) -> dict:
    """Diagnose the incident from its trigger and logs, and hand the Slack message to `post`."""
    # The trigger goes first: the exit code or stop reason is often the best evidence.
    text = (f"=== What was reported ===\n{incident.trigger}\n\n"
            f"=== Logs: {logs_source} ===\n{_tail(logs, LOG_CHAR_BUDGET)}")
    try:
        diagnosis = diagnose(text)
    except (DiagnosisError, openai.APIError) as err:
        log.exception("Diagnosis failed for %s", incident.title)
        post(slack.build_failure_message(incident, str(err), logs_url))
        return {"incident": incident.title, "status": "diagnosis_failed", "error": str(err)}
    post(slack.build_message(incident, diagnosis, logs_url))
    log.info("Posted %s diagnosis for %s", diagnosis["severity"], incident.title)
    return {"incident": incident.title, "status": "posted",
            "severity": diagnosis["severity"], "summary": diagnosis["summary"]}


def _tail(text: str, limit: int) -> str:
    """Keep the most recent lines that fit in `limit` characters."""
    if len(text) <= limit:
        return text
    kept = text[-limit:]
    return "[... earlier log lines omitted ...]\n" + kept[kept.find("\n") + 1:]
