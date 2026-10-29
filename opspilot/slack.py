"""Turn a diagnosis into a Slack message and post it to an incoming webhook."""

import json
import urllib.request

# Emoji for the title and colour of the bar down the message's left side.
SEVERITY_STYLE = {
    "critical": ("🔴", "#E01E5A"),
    "high": ("🟠", "#F2952F"),
    "medium": ("🟡", "#ECB22E"),
    "low": ("🟢", "#2EB67D"),
}
MAX_TEXT = 2900  # Slack rejects block text over 3000 characters


def build_message(incident, diagnosis: dict, logs_url: str | None = None) -> dict:
    emoji, color = SEVERITY_STYLE[diagnosis["severity"]]
    title = f"{emoji} {diagnosis['severity'].upper()}: {diagnosis['affected_service']}"
    steps = "\n".join(f"{n}. {_escape(step)}" for n, step in enumerate(diagnosis["fix_steps"], 1))
    blocks = [
        _header(title),
        _section(f"*{_escape(diagnosis['summary'])}*"),
        _context(f"Trigger: {_escape(incident.title)}  |  Confidence {diagnosis['confidence']:.0%}"
                 "  |  Diagnosed by NVIDIA Nemotron on Nebius"),
        _section("*Root cause*\n" + _escape(diagnosis["root_cause"])),
        _section("*Evidence from the logs*\n" + _code("\n".join(diagnosis["evidence"]))),
        _section("*Fix steps*\n" + steps),
        _section("*Prevention*\n" + "\n".join(f"• {_escape(item)}" for item in diagnosis["prevention"])),
    ]
    if logs_url:
        blocks.append(_context(f"<{logs_url}|Open the logs in CloudWatch>"))
    return {"text": f"{title}: {diagnosis['summary']}", "attachments": [{"color": color, "blocks": blocks}]}


def build_failure_message(incident, error: str, logs_url: str | None = None) -> dict:
    """Still tell on-call what AWS reported when the diagnosis itself fails."""
    title = f"⚠️ OpsPilot could not diagnose: {incident.title}"
    blocks = [
        _header(title),
        _section("*What was reported*\n" + _code(incident.trigger)),
        _section("*Why the diagnosis failed*\n" + _escape(error)),
    ]
    if logs_url:
        blocks.append(_context(f"<{logs_url}|Open the logs in CloudWatch>"))
    return {"text": title, "attachments": [{"color": "#9E9E9E", "blocks": blocks}]}


def post(webhook_url: str, message: dict) -> None:
    request = urllib.request.Request(webhook_url, data=json.dumps(message).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10):
        pass  # urlopen raises HTTPError if Slack rejects the message


def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": _clip(text, 150), "emoji": True}}


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _clip(text)}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": _clip(text)}]}


def _code(text: str) -> str:
    # Clip before adding the fences, so a long block never loses its closing ```.
    return "```" + _clip(_escape(text), MAX_TEXT - 100) + "```"


def _escape(text: str) -> str:
    # Slack treats &, < and > as markup; model output like "heap > limit" must stay literal.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"
