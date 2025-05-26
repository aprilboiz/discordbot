# Stage 1: Build dependencies
FROM python:3.12-alpine AS deps-builder

RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache

RUN uv sync --locked --no-dev --no-cache && \
    apk del .build-deps && \
    rm -rf /tmp/* /var/cache/apk/* /root/.cache && \
    find /app/.venv -name "*.pyc" -delete && \
    find /app/.venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -name "*.pyo" -delete && \
    find /app/.venv -name "test*" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -name "*.egg-info" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -type f -name "*.so" -exec strip {} + 2>/dev/null || true

# Stage 2: Runtime base with system dependencies
FROM python:3.12-alpine AS runtime-base

ENV TZ=Asia/Ho_Chi_Minh \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    LD_LIBRARY_PATH=/usr/lib:/lib \
    OPUS_LIBRARY_PATH=/usr/lib/libopus.so.0 \
    PATH="/app/.venv/bin:$PATH"

# Install essential runtime dependencies
RUN apk add --no-cache \
    nodejs \
    ffmpeg \
    opus \
    tzdata \
    su-exec && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    addgroup -g 1001 -S appgroup && \
    adduser -u 1001 -S appuser -G appgroup && \
    rm -rf /tmp/* /var/cache/apk/*

# Stage 3: Final application image
FROM runtime-base AS final

WORKDIR /app

COPY --from=deps-builder --chown=appuser:appgroup /app/.venv /app/.venv

COPY --chown=appuser:appgroup *.py ./
COPY --chown=appuser:appgroup cogs/ ./cogs/
COPY --chown=appuser:appgroup core/ ./core/
COPY --chown=appuser:appgroup mapper/ ./mapper/
COPY --chown=appuser:appgroup patterns/ ./patterns/
COPY --chown=appuser:appgroup utils/ ./utils/

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && \
    chmod +x /entrypoint.sh && \
    mkdir -p logs temp_folder && \
    chown -R appuser:appgroup /app && \
    chmod -R 755 /app

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import discord.opus; discord.opus.is_loaded() or exit(1); exit(0)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
