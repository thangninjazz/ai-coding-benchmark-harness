# syntax=docker/dockerfile:1.7
#
# Two stages: dependencies are resolved into wheels in a throw-away stage, and the runtime
# image installs them offline (--no-index). The runtime image has no compiler, git, curl or
# pip cache, and the harness code is owned by root and read-only to the runner user.
#
# Network isolation is a *run-time* property - Docker cannot bake it into an image.
# Always run untrusted code with --network none (see README "Docker").
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------- stage 1: wheels
FROM ${PYTHON_IMAGE} AS wheels
WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --no-cache-dir --disable-pip-version-check --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------- stage 2: runtime
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="ai-coding-benchmark-harness" \
      org.opencontainers.image.description="Offline SWE-bench-style evaluation harness" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Unprivileged runner: fixed uid (matches DockerSandbox --user), no home, no login shell.
RUN useradd --uid 1001 --no-create-home --shell /usr/sbin/nologin runner

WORKDIR /opt/harness
COPY harness ./harness
COPY tasks ./tasks

USER 1001:1001

ENTRYPOINT ["python", "-m", "harness.cli"]
CMD ["--help"]
