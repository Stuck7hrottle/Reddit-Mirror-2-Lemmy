FROM python:3.11-slim

WORKDIR /app
COPY auto_mirror.py /app

RUN pip install --no-cache-dir praw requests
ENV PYTHONUNBUFFERED=1
CMD ["python", "-u", "auto_mirror.py"]
