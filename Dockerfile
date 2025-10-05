FROM python:3.11-slim

WORKDIR /app
COPY auto_mirror.py /app

RUN pip install --no-cache-dir praw requests

CMD ["python", "auto_mirror.py"]
