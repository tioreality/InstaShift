# ════════════════════════════════════════════════════
# InstaShift – Dockerfile
# Multi-stage build for a lean production image.
# ════════════════════════════════════════════════════

# ── Stage 1: build ───────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies into a prefix
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="InstaShift Bot" \
      description="Discord bot that mirrors Instagram feeds" \
      version="1.0.0"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY bot/ ./bot/

# ⚠️ LÍNEA ELIMINADA: VOLUME ["/app/data"]  <-- ESTO YA NO DEBE ESTAR

# Non-root user for security
RUN addgroup --system botuser && adduser --system --ingroup botuser botuser
USER botuser

# Environment defaults (override with docker run -e or .env)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data/instashift.db \
    SESSION_PATH=/app/data/ig_session.json \
    LOG_LEVEL=INFO

CMD ["python", "-m", "bot.main"]
