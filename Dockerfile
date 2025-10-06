# Reddit → Lemmy Bridge
# Compatible with Python 3.11 and Lemmy-Ansible setups

FROM python:3.11-slim

# Create and switch to /app
WORKDIR /app

# Copy all project files
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir praw requests python-dotenv

# Ensure Python output is unbuffered for clean logging
ENV PYTHONUNBUFFERED=1

# Default command (can be overridden in docker-compose.yml)
CMD ["python", "-u", "auto_mirror.py"]
