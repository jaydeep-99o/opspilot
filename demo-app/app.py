"""orders-api: a tiny FastAPI service that can be broken on purpose to demo OpsPilot.

Set FAIL_MODE to trigger a realistic incident:
    oom          a cache that never evicts grows until the container is OOM-killed
    missing_env  startup crashes because DATABASE_URL is not set
    bad_health   /health returns 500 (a "deep" check on an unreachable Redis), so a load
                 balancer marks the task unhealthy even though /orders still works
    errors       about half of GET /orders requests crash with a TypeError and stack trace

Unset (or "none") runs normally. The logs never mention FAIL_MODE, so OpsPilot has to work
out what happened from the symptoms, just like in a real incident.
"""

import contextvars
import json
import logging
import os
import random
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SERVICE = "orders-api"
VERSION = os.environ.get("APP_VERSION", "1.4.0")
PORT = int(os.environ.get("PORT", "8080"))
FAIL_MODE = os.environ.get("FAIL_MODE", "").strip().lower()
FAIL_MODES = ("", "none", "oom", "missing_env", "bad_health", "errors")

log = logging.getLogger(SERVICE)

# The id of the request being handled, so every log line written during it can include it.
request_id_var = contextvars.ContextVar("request_id", default=None)


# --- Logging -------------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """One JSON object per line, so CloudWatch Logs can search and filter on each field."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
                                 .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "service": SERVICE,
            "version": VERSION,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id := request_id_var.get():
            entry["request_id"] = request_id
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)  # ECS ships stdout to CloudWatch Logs
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def fields(**values) -> dict:
    """Structured fields for a log line: log.info("Order saved", extra=fields(order_id=7))."""
    return {"fields": values}


# --- Startup -------------------------------------------------------------------------------

def load_settings() -> dict:
    if FAIL_MODE == "missing_env":
        # Incident: the new release needs DATABASE_URL, but the task definition doesn't set it.
        os.environ.pop("DATABASE_URL", None)
    database_url = os.environ["DATABASE_URL"]
    return {"database_host": urlparse(database_url).hostname}


def container_memory_limit_mb() -> int | None:
    """The container's memory limit from cgroups (v2, then v1); None if unlimited or unknown."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as limit_file:
                value = limit_file.read().strip()
        except OSError:
            continue
        # cgroup v2 says "max" and v1 a huge number when there is no limit.
        return int(value) // 2**20 if value.isdigit() and int(value) < 2**60 else None
    return None


def rss_mb() -> int | None:
    """How much memory this process is really using (Linux only)."""
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


order_history_cache = []  # Incident (oom): nothing is ever evicted from this list.


def warm_order_history_cache() -> None:
    """Background job that keeps loading order history into memory and never frees it."""
    batch = 0
    while True:
        batch += 1
        # Fill 20 MB with real bytes; merely reserving it wouldn't count as used memory.
        order_history_cache.append(b"x" * (20 * 2**20))
        log.info("Cached order history batch", extra=fields(
            batch=batch, orders_cached=batch * 25_000, cache_mb=batch * 20, rss_mb=rss_mb()))
        time.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if FAIL_MODE not in FAIL_MODES:
        log.warning("Unknown FAIL_MODE %r, running normally", FAIL_MODE)
    log.info("Loading configuration")
    settings = load_settings()
    log.info("Configuration loaded", extra=fields(
        database_host=settings["database_host"], memory_limit_mb=container_memory_limit_mb()))
    if FAIL_MODE == "oom":
        threading.Thread(target=warm_order_history_cache, daemon=True).start()
        log.info("Started order history cache warmer")
    log.info("orders-api ready", extra=fields(port=PORT))
    yield
    log.info("orders-api shutting down")


app = FastAPI(title="orders-api", lifespan=lifespan)


# --- Request logging -----------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Give every request an id, log how it went, and turn crashes into a logged 500."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            log.exception("Unhandled exception while handling request",
                          extra=fields(method=request.method, path=request.url.path))
            response = JSONResponse({"error": "internal server error", "request_id": request_id},
                                    status_code=500)
        status = response.status_code
        log.log(logging.ERROR if status >= 500 else logging.INFO,
                f"{request.method} {request.url.path} {status}",
                extra=fields(method=request.method, path=request.url.path, status=status,
                             duration_ms=round((time.perf_counter() - started) * 1000, 1),
                             client_ip=request.client.host if request.client else None,
                             user_agent=request.headers.get("user-agent")))
        response.headers["x-request-id"] = request_id
        return response
    finally:
        request_id_var.reset(token)


# --- Endpoints -----------------------------------------------------------------------------

ORDERS = [
    {"order_id": "ord_1001", "customer_id": "cus_381", "status": "shipped",
     "items": [{"sku": "KB-104", "quantity": 1, "unit_price": 89.0},
               {"sku": "MS-220", "quantity": 2, "unit_price": 24.5}]},
    {"order_id": "ord_1002", "customer_id": "cus_117", "status": "processing",
     "items": [{"sku": "MON-27Q", "quantity": 1, "unit_price": 319.99}]},
    {"order_id": "ord_1003", "customer_id": "cus_642", "status": "pending",
     "items": [{"sku": "USB-C-HUB", "quantity": 3, "unit_price": 39.0}]},
]


def load_recent_orders() -> list[dict]:
    """Stand-in for a database query."""
    orders = list(ORDERS)
    if FAIL_MODE == "errors" and random.random() < 0.5:
        # Incident: rows from a legacy catalog import have a NULL unit_price.
        orders.append({"order_id": f"ord_{random.randint(5000, 5999)}", "customer_id": "cus_902",
                       "status": "pending",
                       "items": [{"sku": "LEGACY-7731", "quantity": 2, "unit_price": None}]})
    return orders


def order_total(order: dict) -> float:
    return round(sum(item["quantity"] * item["unit_price"] for item in order["items"]), 2)


@app.get("/orders")
def list_orders():
    orders = load_recent_orders()
    return {"count": len(orders), "orders": [{**order, "total": order_total(order)} for order in orders]}


@app.get("/health")
def health():
    if FAIL_MODE == "bad_health":
        # Incident: a "deep" health check also pings Redis, which is unreachable, so every
        # check fails even though /orders doesn't need Redis and still works.
        time.sleep(2)
        log.error("Health check failed: dependency unreachable", extra=fields(
            dependency="redis", host="orders-cache.internal:6379",
            error="Timeout connecting to server after 2000 ms"))
        return JSONResponse({"status": "unhealthy", "checks": {"redis": "timeout"}}, status_code=500)
    return {"status": "ok", "version": VERSION}


if __name__ == "__main__":
    setup_logging()
    # log_config=None keeps uvicorn's own messages in our JSON format. The middleware above
    # already logs every request, so uvicorn's access log is turned off.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_config=None, access_log=False)
