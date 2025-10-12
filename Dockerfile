# === Reddit-Mirror-2-Lemmy Production Dockerfile ===
FROM python:3.11-slim

# Set working directory
WORKDIR /opt/Reddit-Mirror-2-Lemmy

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl sqlite3 bash && \
    rm -rf /var/lib/apt/lists/*

# Copy project source
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Ensure persistent data directory exists
RUN mkdir -p data

# Environment configuration
ENV PYTHONUNBUFFERED=1 \
    TZ=UTC

# Declare volume for persistence
VOLUME ["/opt/Reddit-Mirror-2-Lemmy/data"]

# Default command (overridden by docker-compose)
CMD ["python3", "background_worker.py"]
