# Stage 1: Build Python dependencies
FROM python:3.12-slim-bookworm AS python-builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml uv.lock ./

RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime environment
FROM python:3.12-slim-bookworm

# Set timezone
ENV TZ=Asia/Ho_Chi_Minh

WORKDIR /app

# Install runtime dependencies in a single step
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg &&\
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from the builder stage
COPY --from=python-builder /install /usr/local

# Copy application code
COPY . .

# Set the default command
CMD ["python", "main.py"]