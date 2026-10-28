# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
# Slow or flaky connections: long timeout, retries, and a download cache kept between builds,
# so a failed build resumes instead of downloading everything again.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt
COPY mb mb
COPY config config
COPY prompts prompts
COPY data/itineraries data/itineraries
COPY data/fixtures data/fixtures
COPY data/preferences.yaml data/trips.yaml data/
EXPOSE 8000
CMD ["uvicorn", "mb.api:app", "--host", "0.0.0.0", "--port", "8000"]
