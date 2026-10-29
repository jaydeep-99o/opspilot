"""Run the OpsPilot Docker watcher: diagnose failing containers and post the diagnosis to Slack.

Usage:
    docker compose up -d           # runs this watcher in a container, next to the demo app
    python watch_docker.py         # or run it directly on your machine

Watches containers labelled opspilot.watch=true. Reads NEBIUS_* and SLACK_WEBHOOK_URL from the
environment (.env); without SLACK_WEBHOOK_URL the alerts are printed instead of posted.
"""

import json
import logging
import os
import sys
from pathlib import Path

import docker
from dotenv import load_dotenv

from opspilot import slack
from opspilot.docker_watch import WATCH_LABEL, Watcher

READY_FILE = Path("/tmp/opspilot-ready")  # the compose health check waits for this file


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        post = lambda message: slack.post(webhook, message)  # noqa: E731
    else:
        logging.warning("SLACK_WEBHOOK_URL is not set; alerts will be printed instead of posted")
        post = lambda message: print(json.dumps(message, indent=2, ensure_ascii=False))  # noqa: E731

    watcher = Watcher(docker.from_env(), post, os.environ.get("WATCH_LABEL", WATCH_LABEL))
    watcher.run(on_ready=lambda: READY_FILE.touch() if os.name != "nt" else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
