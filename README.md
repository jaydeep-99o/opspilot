# OpsPilot

AI incident triage: paste in error logs, get back a structured diagnosis from an NVIDIA
Nemotron model running on Nebius Token Factory.

```json
{
  "affected_service": "order-processor",
  "root_cause": "...",
  "severity": "high",
  "confidence": 0.86,
  "fix_steps": ["...", "..."],
  "summary": "..."
}
```

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
```

## Project layout

| Path | What it does |
| --- | --- |
| `opspilot/diagnose.py` | `diagnose(logs) -> dict`: prompts the model, extracts and validates the JSON, retries once if invalid. Reads config only from environment variables, so it can be reused as-is in AWS Lambda. |
| `run_local.py` | Local runner: loads `.env`, diagnoses a log file and pretty-prints the result. |
| `samples/` | Realistic incidents: ECS task OOM-killed, Python app missing an env var, failing ALB health check. |
| `.env.example` | Template for `NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, `NEBIUS_MODEL`. Copy it to `.env` (which is git-ignored). |

## License

[MIT](LICENSE)
