FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Moscow

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Не работаем из-под root. Папку для фото создаём заранее: пустой
# том Docker примет её владельца, и бот сможет туда писать.
RUN useradd -m -u 1000 bot && mkdir -p /app/storage && chown -R bot:bot /app
USER bot

CMD ["python", "-m", "app.main"]
