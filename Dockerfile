# === Reddit-Mirror-2-Lemmy Production Dockerfile (v2) ===
FROM python:3.11-slim

# Set working directory
WORKDIR /opt/Reddit-Mirror-2-Lemmy

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl sqlite3 bash ffmpeg yt-dlp && \
    rm -rf /var/lib/apt/lists/*

# Copy project source
COPY . .

# Install Python dependencies (includes python-dotenv in requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Ensure persistent data directory exists
RUN mkdir -p /opt/Reddit-Mirror-2-Lemmy/data

# Environment configuration
ENV PYTHONUNBUFFERED=1 \
    TZ=UTC \
    DOTENV_PATH=/opt/Reddit-Mirror-2-Lemmy/.env

# Declare volume for persistence
VOLUME ["/opt/Reddit-Mirror-2-Lemmy/data"]

# Optional health check: confirms DB is reachable
HEALTHCHECK --interval=2m --timeout=10s --retries=3 \
  CMD python3 -c "import sqlite3; sqlite3.connect('/opt/Reddit-Mirror-2-Lemmy/data/jobs.db').execute('SELECT 1')" || exit 1

# Default command (overridden by docker-compose)
CMD ["python3", "background_worker.py"]
