FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY scripts/start.sh ./scripts/start.sh

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

# Free hosts (Render, Koyeb) pass the port in $PORT; 8000 locally.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
