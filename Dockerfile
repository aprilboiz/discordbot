FROM python:3.12-alpine AS builder

RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    cargo \
    rust

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

# Install dependencies to a virtual environment with optimizations
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_CACHE_DIR=/tmp/uv-cache
RUN uv sync --locked --no-dev --no-cache && \
    apk del .build-deps && \
    rm -rf /tmp/* /var/cache/apk/* /root/.cache /root/.cargo && \
    find /app/.venv -name "*.pyc" -delete && \
    find /app/.venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -name "*.pyo" -delete && \
    find /app/.venv -name "test*" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -name "*.egg-info" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -type f -name "*.so" -exec strip {} + 2>/dev/null || true

# Final stage - minimal runtime image
FROM python:3.12-alpine

ENV TZ=Asia/Ho_Chi_Minh
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

RUN apk add --no-cache \
    nodejs \
    ffmpeg \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /tmp/* /var/cache/apk/* /usr/share/man /usr/share/doc /var/lib/apk/* \
    && find /usr -name "*.a" -delete \
    && find /usr -name "*.la" -delete

# Create non-root user for security
RUN addgroup -g 1001 -S appgroup && \
    adduser -u 1001 -S appuser -G appgroup

# Set working directory
WORKDIR /app

# Copy virtual environment from builder and set ownership
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code (exclude unnecessary files)
COPY --chown=appuser:appgroup *.py ./
COPY --chown=appuser:appgroup cogs/ ./cogs/
COPY --chown=appuser:appgroup core/ ./core/
COPY --chown=appuser:appgroup mapper/ ./mapper/
COPY --chown=appuser:appgroup patterns/ ./patterns/
COPY --chown=appuser:appgroup utils/ ./utils/

# Create required directories with proper permissions and ownership
RUN mkdir -p logs temp_folder && \
    chown -R appuser:appgroup /app && \
    chmod -R 755 /app && \
    find /app -type f -name "*.py" -exec chmod 644 {} + && \
    find /app -type d -exec chmod 755 {} +

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "main.py"]