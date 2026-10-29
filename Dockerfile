# The OpsPilot Docker watcher. Secrets come from .env at run time (see docker-compose.yml);
# .dockerignore keeps .env out of the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY opspilot/ opspilot/
COPY watch_docker.py .

# Healthy once the watcher is listening to Docker events, so the demo app can wait for it.
HEALTHCHECK --interval=2s --timeout=2s --retries=30 CMD test -f /tmp/opspilot-ready

# Runs as root because it talks to the Docker socket, which root owns.
CMD ["python", "watch_docker.py"]
