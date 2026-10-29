"""Watch local Docker containers and diagnose their failures: free, no cloud account needed.

The local equivalents of the AWS triggers:
    container died (exit code, OOM kill)        ~ ECS "task stopped" event
    container became unhealthy (HEALTHCHECK)     ~ load balancer health checks failing
    many error lines in the last minute          ~ CloudWatch 5xx alarm
Only containers labelled opspilot.watch=true are watched.
"""

import logging
import re
import threading
import time
from datetime import datetime, timedelta

import docker.errors

from opspilot.events import Incident
from opspilot.pipeline import report, seen_recently

log = logging.getLogger("opspilot")

WATCH_LABEL = "opspilot.watch=true"
LOOKBACK = timedelta(minutes=15)
ERROR_WINDOW_SECONDS = 60
ERROR_THRESHOLD = 10
POLL_SECONDS = 15
MANUAL_STOP_SECONDS = 30  # a container dying this soon after `docker stop`/`kill` was stopped on purpose

ERROR_LINE = re.compile(r"\b(ERROR|CRITICAL|FATAL)\b|Traceback \(most recent call last\)")


def died_incident(attrs: dict) -> Incident | None:
    """An incident for a container that exited with an error or was OOM-killed (None if it exited cleanly)."""
    state = attrs["State"]
    exit_code, oom_killed = state.get("ExitCode"), state.get("OOMKilled", False)
    if exit_code == 0 and not oom_killed:
        return None
    name = _name(attrs)
    memory = attrs.get("HostConfig", {}).get("Memory") or 0
    lines = [
        f"Docker container {name} (image {attrs['Config']['Image']}) exited",
        f"Exit code: {exit_code}",
        f"OOMKilled: {str(oom_killed).lower()}",
        f"Memory limit: {memory // 2**20} MiB" if memory else "Memory limit: none",
        f"Restart count: {attrs.get('RestartCount', 0)}",
        f"Started at {state.get('StartedAt')}, finished at {state.get('FinishedAt')}",
    ]
    if state.get("Error"):
        lines.append(f"Docker error: {state['Error']}")
    what = "OOM-killed" if oom_killed else f"exit code {exit_code}"
    return _incident(attrs, f"Container died: {name} ({what})", lines)


def unhealthy_incident(attrs: dict) -> Incident:
    name = _name(attrs)
    health = attrs["State"].get("Health") or {}
    last_check = (health.get("Log") or [{}])[-1]
    command = attrs["Config"].get("Healthcheck", {}).get("Test", [])
    if command[:1] in (["CMD"], ["CMD-SHELL"]):
        command = command[1:]
    lines = [
        f"Docker health check reports container {name} (image {attrs['Config']['Image']}) as unhealthy",
        f"Health check command: {' '.join(command)}",
        f"Failing streak: {health.get('FailingStreak')}",
        f"Last check exit code: {last_check.get('ExitCode')}",
        f"Last check output: {(last_check.get('Output') or '').strip()[-800:]}",
    ]
    return _incident(attrs, f"Container unhealthy: {name}", lines)


def error_rate_incident(attrs: dict, error_lines: int) -> Incident:
    name = _name(attrs)
    lines = [
        f"Error-rate alarm: container {name} logged {error_lines} error lines in the last "
        f"{ERROR_WINDOW_SECONDS} seconds (threshold {ERROR_THRESHOLD})",
        f"Image {attrs['Config']['Image']}, running since {attrs['State'].get('StartedAt')}",
    ]
    return _incident(attrs, f"Error-rate alarm: {name} ({error_lines} errors in {ERROR_WINDOW_SECONDS}s)", lines)


def count_error_lines(logs: str) -> int:
    return sum(1 for line in logs.splitlines() if ERROR_LINE.search(line))


class Watcher:
    """Follows Docker's event stream, plus a periodic error-rate check, and reports incidents."""

    def __init__(self, client, post, label: str = WATCH_LABEL):
        self.client, self.post, self.label = client, post, label
        self.lock = threading.Lock()  # one diagnosis at a time; the event and error-rate threads share it
        self.stop_requested: dict[str, float] = {}  # container id -> when someone ran docker stop/kill

    def run(self, on_ready=lambda: None) -> None:
        threading.Thread(target=self.watch_error_rates, daemon=True).start()
        events = self.client.events(decode=True, filters={"type": "container", "label": self.label})
        log.info("Watching containers labelled %s", self.label)
        on_ready()
        for event in events:
            self.on_event(event)

    def on_event(self, event: dict) -> None:
        action, container_id = event.get("Action", ""), event.get("Actor", {}).get("ID", "")
        if action == "kill":
            # docker stop, kill and rm -f all send a signal first; the kernel's OOM killer does not.
            self.stop_requested[container_id] = time.time()
        elif action == "die":
            container = self._get(container_id)
            if container is None:
                return
            stopped_on_purpose = time.time() - self.stop_requested.pop(container_id, 0) < MANUAL_STOP_SECONDS
            if stopped_on_purpose and not container.attrs["State"].get("OOMKilled"):
                return
            self.handle(died_incident(container.attrs), container)
        elif action.startswith("health_status") and "unhealthy" in action:
            container = self._get(container_id)
            if container is not None:
                self.handle(unhealthy_incident(container.attrs), container)

    def watch_error_rates(self) -> None:
        while True:
            time.sleep(POLL_SECONDS)
            try:
                for container in self.client.containers.list(filters={"label": self.label}):
                    since = int(time.time() - ERROR_WINDOW_SECONDS)
                    errors = count_error_lines(container.logs(since=since).decode("utf-8", "replace"))
                    if errors >= ERROR_THRESHOLD:
                        self.handle(error_rate_incident(container.attrs, errors), container)
            except Exception:
                log.exception("Error-rate check failed; will try again")

    def handle(self, incident: Incident | None, container) -> dict | None:
        if incident is None:
            return None
        with self.lock:
            if seen_recently(incident.dedup_key):
                return None
            log.info("Incident: %s", incident.title)
            since = int(time.time() - LOOKBACK.total_seconds())
            logs = container.logs(since=since).decode("utf-8", "replace")
            result = report(incident, logs, f"docker logs {_name(container.attrs)}", self.post)
            log.info("Result: %s", result)
            return result

    def _get(self, container_id: str):
        try:
            return self.client.containers.get(container_id)
        except docker.errors.NotFound:  # e.g. started with --rm and already removed
            log.warning("Container %s is gone; its logs can't be read", container_id[:12])
            return None


def _incident(attrs: dict, title: str, lines: list[str]) -> Incident:
    now = datetime.now().astimezone()
    return Incident(
        title=title,
        trigger="\n".join(lines),
        region="",
        service=_name(attrs),
        start=now - LOOKBACK,
        end=now,
        log_stream=None,
        # One alert per container: a crash loop, or a container that is both unhealthy and
        # erroring, is posted once. A recreated container (new id) gets its own alert.
        dedup_key=f"docker:{_name(attrs)}:{attrs['Id'][:12]}",
    )


def _name(attrs: dict) -> str:
    return attrs["Name"].lstrip("/")
