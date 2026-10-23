# Hermetic & Reproducible Dockerfile for AI Benchmark Execution
# Compliant with SWE-bench sandbox standards: No network access during test runs
FROM python:3.11-slim-bullseye AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /workspace

# Install minimal build essentials & security dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    patch \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install pinned Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root unprivileged runner for security
RUN useradd -m -u 1001 benchmark_runner && \
    chown -R benchmark_runner:benchmark_runner /workspace

USER benchmark_runner

# Copy benchmark harness and task definitions
COPY --chown=benchmark_runner:benchmark_runner . .

ENTRYPOINT ["python", "-m", "harness.cli"]
CMD ["--help"]
