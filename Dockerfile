FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal; SQLite ships with Python's stdlib, no extra package needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY .env.example .env.example

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

# gunicorn manages the uvicorn worker(s) — the standard production setup for FastAPI.
# Pinned to 1 worker: SQLite doesn't handle concurrent writers from multiple
# processes well, and 2+ workers would race to create/seed the database on
# first startup (confirmed to cause the whole app to fail to boot). If you
# switch DATABASE_URL to Postgres (see docker-compose.yml), it's then safe
# to raise --workers to 2-4 for more throughput.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "1", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
