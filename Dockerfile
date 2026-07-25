# syntax=docker/dockerfile:1

# --- build the wheels -------------------------------------------------------
FROM python:3.12-slim AS build

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# --- runtime ---------------------------------------------------------------
FROM python:3.12-slim

# opencv-python-headless still needs glib even without any GUI backend.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 bot
WORKDIR /app

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY src/ /app/src/
COPY templates/ /app/templates/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHENZHEN_TEMPLATES=/app/templates/default

USER bot

# Long-polling only -- no inbound port, so this runs anywhere with outbound
# HTTPS and needs no reverse proxy or certificate.
CMD ["python", "-m", "shenzhen.bot.main"]
