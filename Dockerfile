# Reddit → Lemmy Bridge
# Compatible with Python 3.12 and Lemmy-Ansible setups

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy dependency files first (for Docker layer caching)
COPY requirements.txt ./

# Install Python dependencies
# Include praw, requests, python-dotenv and any other required packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . /app

# Ensure /app/data exists for caching, tokens, etc.
RUN mkdir -p /app/data

# Environment variables for stable runtime
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# Run as non-root user for security (optional but professional)
RUN useradd -m bridgeuser
USER bridgeuser

# Default startup command (overrideable by docker-compose.yml)
CMD ["python3", "-u", "auto_mirror.py"]
