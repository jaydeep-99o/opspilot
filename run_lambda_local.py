"""Run the OpsPilot Lambda locally: a sample AWS event + a local log file -> Slack message.

Usage:
    python run_lambda_local.py samples/events/ecs_task_stopped_oom.json samples/docker/oom.log
    python run_lambda_local.py samples/events/alarm_5xx_sns.json samples/docker/errors.log --post

The log file stands in for CloudWatch Logs. Without --post the Slack message is printed;
with --post it is sent to SLACK_WEBHOOK_URL from .env.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from opspilot import slack
from opspilot.handler import handle


class FileLogsClient:
    """Stands in for the CloudWatch Logs client by serving the lines of a local file."""

    def __init__(self, path: Path):
        self.lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def filter_log_events(self, **params) -> dict:
        return {"events": [{"timestamp": n, "message": line} for n, line in enumerate(self.lines)]}


def preview(message: dict) -> None:
    """Print a Slack message roughly as it will look."""
    print(f"\n[Slack message] {message['text']}\n" + "-" * 88)
    for block in message["attachments"][0]["blocks"]:
        if block["type"] == "context":
            print("  " + " ".join(element["text"] for element in block["elements"]))
        else:
            print(block["text"]["text"])
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OpsPilot Lambda handler locally.")
    parser.add_argument("event", type=Path, help="AWS event JSON (see samples/events/)")
    parser.add_argument("logs", type=Path, help="log file standing in for CloudWatch Logs")
    parser.add_argument("--post", action="store_true",
                        help="send the message to SLACK_WEBHOOK_URL instead of printing it")
    parser.add_argument("--log-group", default="ecs/orders-api",
                        help="CloudWatch log group name for the message and console link, without "
                             "the leading slash (default: ecs/orders-api)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    # Added here rather than typed by the user: Git Bash rewrites arguments starting with "/".
    os.environ["LOG_GROUP"] = "/" + args.log_group.lstrip("/")
    if args.post and not os.environ.get("SLACK_WEBHOOK_URL"):
        print("Error: set SLACK_WEBHOOK_URL in .env to use --post", file=sys.stderr)
        return 1
    post = (lambda message: slack.post(os.environ["SLACK_WEBHOOK_URL"], message)) if args.post else preview

    event = json.loads(args.event.read_text(encoding="utf-8"))
    results = handle(event, FileLogsClient(args.logs), post)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
