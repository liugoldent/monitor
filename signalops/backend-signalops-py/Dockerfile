FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend-signalops-py/pyproject.toml backend-signalops-py/README.md ./
COPY backend-signalops-py/src ./src
COPY backend-signalops-py/alembic.ini ./
COPY backend-signalops-py/migrations ./migrations

RUN pip install --no-cache-dir .

RUN groupadd --system signalops && useradd --system --gid signalops signalops

USER signalops

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn signalops.main:app --host 0.0.0.0 --port 8000"]
