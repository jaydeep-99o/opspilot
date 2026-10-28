# OpsPilot

AI incident triage: paste in error logs, get back a structured diagnosis from an NVIDIA
Nemotron model running on Nebius Token Factory.

```json
{
  "affected_service": "checkout-web",
  "root_cause": "The ALB target group health-checks /health, but the app only serves /healthz ...",
  "evidence": ["\"GET /health HTTP/1.1\" 404 19 \"-\" \"ELB-HealthChecker/2.0\" 1.2ms", "..."],
  "severity": "critical",
  "confidence": 0.95,
  "fix_steps": ["aws elbv2 modify-target-group --target-group-arn ... --health-check-path /healthz", "..."],
  "prevention": ["Validate health-check paths against app routes in CI", "..."],
  "summary": "Checkout-web down: ALB health checks hit /health, app serves /healthz"
}
```

Every `evidence` line is checked against the original logs, and any quote the model made up
is dropped. A diagnosis can only cite lines that really happened.

## Step 1: run it locally

Requires Python 3.10+ and a Nebius Token Factory API key.

```powershell
# 1. Create a virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure credentials
copy .env.example .env              # macOS/Linux: cp .env.example .env
#    then edit .env and paste your NEBIUS_API_KEY

# 3. Check which Nemotron model IDs your key can use, and put one in NEBIUS_MODEL
python run_local.py --list-models

# 4. Diagnose a sample incident
python run_local.py samples/ecs_oom.log
python run_local.py samples/missing_env_var.log
python run_local.py samples/alb_health_check.log --json

# 5. Optional: compare models side by side (writes comparison.json)
python compare_models.py
```

## Model choice

`compare_models.py` runs the same prompt and logs through each model. Results on the three
sample incidents (Oct 27, 2026):

| Model | ALB outage (expect critical) | Repeated OOM kills (expect high) | Rolled-back deploy (expect medium) | Avg time | Cost per diagnosis |
| --- | --- | --- | --- | --- | --- |
| **Nemotron 3 Super 120B-A12B** | critical ✅ | high ✅ | medium ✅ | ~10 s | ~$0.0025 |
| Nemotron 3 Nano 30B-A3B | high ❌ | high ✅ | high ❌ | ~28 s | ~$0.0007 |

Both models found the right root cause each time. Super also ranked severity correctly, quoted
more precise evidence, and gave runnable AWS CLI fixes, so OpsPilot uses
`nvidia/nemotron-3-super-120b-a12b`. Switching models only means changing `NEBIUS_MODEL`.

## Steps 3–4: the Lambda and the Slack alert

`opspilot/handler.py` is the AWS Lambda (handler `opspilot.handler.lambda_handler`). Two things trigger it:

- an EventBridge rule for ECS tasks that stop unexpectedly (OOM kill, crash, failed health checks)
- a CloudWatch alarm delivered through SNS (for example, too many 5xx responses)

For each incident, the Lambda does four things:

1. Reads the stopped task's recent CloudWatch logs (or the whole service's, for alarms).
2. Puts what AWS reported (stop reason, exit code, alarm reason) in front of those logs.
3. Asks Nemotron for a diagnosis.
4. Posts the diagnosis to Slack, colour-coded by severity, with a link to the logs.

Deploys, scale-ins and alarms going back to OK are ignored. A crash loop is posted only once
every 10 minutes. If the diagnosis fails, Slack still gets what AWS reported. The Nebius key and
Slack webhook come from SSM Parameter Store.

Try it locally with the sample AWS events and real logs from the demo app; no AWS account is needed:

```powershell
python run_lambda_local.py samples/events/ecs_task_stopped_oom.json samples/docker/oom.log
python run_lambda_local.py samples/events/alarm_5xx_sns.json samples/docker/errors.log
# to send it to Slack, put SLACK_WEBHOOK_URL in .env and add --post
```

Run the tests (they never call the real API):

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

## Project layout

| Path | What it does |
| --- | --- |
| `opspilot/diagnose.py` | `diagnose(logs) -> dict`: prompts the model, extracts and validates the JSON, checks evidence quotes against the logs, retries once if invalid. Reads config only from environment variables, so it can be reused as-is in AWS Lambda. |
| `opspilot/events.py` | Turns ECS task-stopped events and CloudWatch alarms (direct or via SNS) into incidents; ignores deploys and OK alarms. |
| `opspilot/logs.py` | Reads the incident's log lines from CloudWatch Logs and builds the console link. |
| `opspilot/slack.py` | Formats the diagnosis as a Slack message and posts it to an incoming webhook. |
| `opspilot/handler.py` | The Lambda: event → logs → diagnosis → Slack, with secrets from SSM and crash-loop de-duplication. |
| `run_local.py` | Local runner: loads `.env`, diagnoses a log file and pretty-prints the result. |
| `run_lambda_local.py` | Runs the Lambda handler locally on a sample event, with a log file standing in for CloudWatch. |
| `tests/` | pytest suite for event parsing, Slack formatting, the handler and `diagnose()`, using fakes instead of AWS and the model. |
| `compare_models.py` | Runs every sample through several models and reports severity, time, tokens and cost. |
| `samples/` | Realistic incidents: ECS task OOM-killed, Python app missing an env var, failing ALB health check. `samples/events/` holds sample AWS events and `samples/docker/` real logs from each demo-app failure mode. |
| `demo-app/` | `orders-api`, a small FastAPI service with `FAIL_MODE` switches (oom, missing_env, bad_health, errors) that cause real incidents. See its [README](demo-app/README.md). |
| `.env.example` | Template for `NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, `NEBIUS_MODEL`. Copy it to `.env` (which is git-ignored). |

## License

[MIT](LICENSE)
