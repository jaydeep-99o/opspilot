FROM python:3.12-slim

# Unbuffered output: each log line reaches `docker logs` / CloudWatch immediately. Without
# this, the last lines before an OOM kill would be lost in a buffer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

# Normal configuration (the ECS task definition will set these too). No password: the demo
# never actually connects to a database.
ENV PORT=8080 \
    APP_VERSION=1.4.0 \
    DATABASE_URL=postgresql://orders-db.internal:5432/orders

# Lets `docker ps` show (healthy)/(unhealthy) locally, like a load balancer health check.
# ECS ignores this and uses the health check from the task definition / target group.
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=2 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=4)"

# Don't run as root inside the container.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8080
CMD ["python", "app.py"]
