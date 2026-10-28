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

## Project layout

| Path | What it does |
| --- | --- |
| `opspilot/diagnose.py` | `diagnose(logs) -> dict`: prompts the model, extracts and validates the JSON, checks evidence quotes against the logs, retries once if invalid. Reads config only from environment variables, so it can be reused as-is in AWS Lambda. |
| `run_local.py` | Local runner: loads `.env`, diagnoses a log file and pretty-prints the result. |
| `compare_models.py` | Runs every sample through several models and reports severity, time, tokens and cost. |
| `samples/` | Realistic incidents: ECS task OOM-killed, Python app missing an env var, failing ALB health check. |
| `demo-app/` | `orders-api`, a small FastAPI service with `FAIL_MODE` switches (oom, missing_env, bad_health, errors) that cause real incidents. See its [README](demo-app/README.md). |
| `.env.example` | Template for `NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, `NEBIUS_MODEL`. Copy it to `.env` (which is git-ignored). |

## License

[MIT](LICENSE)
