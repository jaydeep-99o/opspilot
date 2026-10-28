# orders-api (OpsPilot demo app)

A tiny FastAPI service that works normally until you flip a switch. Each switch causes a
realistic incident, which gives OpsPilot real logs to diagnose. Nothing here creates AWS resources.

| Endpoint | Normal response |
| --- | --- |
| `GET /health` | `{"status": "ok", "version": "1.4.0"}` |
| `GET /orders` | Three recent orders with their totals |

Logs are JSON lines on stdout (timestamp, level, service, version, request_id, plus details),
which is what ECS ships to CloudWatch Logs. They never mention `FAIL_MODE`, so OpsPilot has to
work out each incident from its symptoms.

## Failure switches

| `FAIL_MODE` | What happens | What the logs show | On ECS this becomes |
| --- | --- | --- | --- |
| `oom` | A background job caches order history in memory and never frees it, growing 20 MB/s until the container is killed. | `Cached order history batch` with rising `cache_mb` / `rss_mb`, then the log just stops. | Task stopped: `OutOfMemoryError: Container killed due to memory usage`, exit code 137. |
| `missing_env` | Startup reads `DATABASE_URL`, which isn't set. | `KeyError: 'DATABASE_URL'` stack trace, then `Application startup failed`. | Task exits with code 3 right after starting; the deployment fails. |
| `bad_health` | `/health` also checks Redis, which is unreachable, so it returns 500 after 2 s. `/orders` still works. | `Health check failed: dependency unreachable` on every health check. | The target group marks tasks unhealthy and ECS keeps replacing them; the ALB returns 503s. |
| `errors` | About half of `GET /orders` requests include a legacy order with no price, and the total calculation crashes. | A `TypeError` stack trace with the `request_id` for each 500. | The target group's 5xx alarm fires. |

## Run it locally with Docker

Run these from the repo root in PowerShell. Use `curl.exe`, because in Windows PowerShell plain
`curl` is a different command. The app listens on port 8080 inside the container (that's what
ECS uses); `-p 8081:8080` publishes it on port 8081 of your machine, because 8080 is often taken
locally (by Jenkins, for example).

```powershell
docker build -t orders-api demo-app
```

**Normal**

```powershell
docker run -d --rm --name orders-api -p 8081:8080 orders-api
curl.exe http://localhost:8081/health
curl.exe http://localhost:8081/orders
docker logs orders-api
docker rm -f orders-api
```

**`oom`**: `--memory=256m` is the container's memory limit (like task memory on ECS). Setting
`--memory-swap` to the same value turns swap off, so the container is killed instead of slowing down.

```powershell
docker run -d --name orders-api --memory=256m --memory-swap=256m -e FAIL_MODE=oom orders-api
docker logs -f orders-api     # cache_mb climbs; the log stops after about 10 s
docker inspect orders-api --format "OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}"
docker rm orders-api
```

Expect `OOMKilled=true ExitCode=137`.

**`missing_env`**: the container prints the crash and exits on its own with code 3.

```powershell
docker run --name orders-api -e FAIL_MODE=missing_env orders-api
docker rm orders-api
```

**`bad_health`**

```powershell
docker run -d --name orders-api -p 8081:8080 -e FAIL_MODE=bad_health orders-api
curl.exe -i http://localhost:8081/health     # 500 after 2 seconds
curl.exe http://localhost:8081/orders        # still 200
docker ps                                    # after ~30 s STATUS shows (unhealthy)
docker rm -f orders-api
```

**`errors`**

```powershell
docker run -d --name orders-api -p 8081:8080 -e FAIL_MODE=errors orders-api
1..20 | ForEach-Object { curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8081/orders }
docker logs orders-api
docker rm -f orders-api
```

## Diagnose an incident with OpsPilot

Before removing the container, save its logs and run them through OpsPilot (from the repo root):

```powershell
docker logs orders-api | Out-File -Encoding utf8 $env:TEMP\incident.log
.venv\Scripts\python run_local.py $env:TEMP\incident.log
```
