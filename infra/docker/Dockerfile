# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for the AI SDLC Orchestrator.
# Stage 1: builder — installs the package and its dependencies.
# Stage 2: runtime — minimal image with only the installed package.
#
# Usage:
#   docker build -f infra/docker/Dockerfile \
#     --build-arg VERSION=$(git describe --tags --always 2>/dev/null || echo '0.0.0') \
#     -t ai-sdlc-orchestrator:latest .
#
# The VERSION build-arg bypasses hatch-vcs's requirement for a .git directory
# (which is excluded by .dockerignore to keep the image lean).

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Install build tools needed by hatchling / native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Accept the version string computed by build-image.sh from git describe.
# This prevents the hatch-vcs plugin from failing when .git is absent.
ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}
# hatch-vcs / hatchling read this env var to bypass VCS detection
ENV HATCH_VCS_PRETEND_VERSION=${VERSION}

# Copy only the files needed to install the package.
# pyproject.toml defines the build metadata; src/ contains the package.
COPY pyproject.toml ./
COPY src/ ./src/

# Install the core orchestrator package (no optional extras needed at runtime).
# --no-cache-dir keeps the layer small; the package is installed into
# /usr/local/lib/python3.11/site-packages/ for copying to the runtime stage.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir .

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Create a non-root user and group for running the orchestrator.
# UID/GID 1000 is the conventional first non-root user on Linux.
RUN groupadd --gid 1000 orchestrator && \
    useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false orchestrator

# Copy only the installed Python packages from the builder stage.
# This avoids copying build tools, .git, or build artifacts.
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/orchestrate* /usr/local/bin/

# Copy the config directory (default.yaml is read at runtime by load_config()).
COPY config/ /app/config/

# Resolve DEFAULT_CONFIG_PATH for the installed (non-editable) package.
#
# load_config() computes its default path as:
#   Path(config.py).resolve().parents[2] / "config" / "default.yaml"
# When the package is installed to site-packages, config.py lives at:
#   /usr/local/lib/python3.11/site-packages/orchestrator/config.py
# parents[2] is therefore /usr/local/lib/python3.11, so DEFAULT_CONFIG_PATH
# resolves to /usr/local/lib/python3.11/config/default.yaml.
# A symlink from that directory to /app/config makes the default path work
# without requiring --config to be passed on every invocation.
RUN python3 -c "\
from pathlib import Path; \
import orchestrator.config as m; \
target_dir = Path(m.__file__).resolve().parents[2] / 'config'; \
target_dir.mkdir(exist_ok=True); \
link = target_dir / 'default.yaml'; \
link.symlink_to('/app/config/default.yaml') if not link.exists() else None; \
print(f'config symlink: {link} -> /app/config/default.yaml')"

# The container workspace is ephemeral — artifacts are written to
# /workspace/runs/<run_id>/ which is bind-mounted from the host.
# /tmp is a tmpfs mount (declared in docker run / docker-compose).
# /home/orchestrator/.config is a tmpfs mount for any tool config caches.
RUN mkdir -p /workspace /app && \
    chown -R orchestrator:orchestrator /workspace /app

WORKDIR /app

# Switch to non-root user for all subsequent operations and at runtime.
USER orchestrator

# Default command: print help (the actual orchestration is triggered by
# ContainerRuntime which passes `orchestrate <feature>` as the CMD override).
CMD ["orchestrate", "--help"]
