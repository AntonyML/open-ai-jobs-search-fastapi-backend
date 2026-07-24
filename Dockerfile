# Dockerfile for FastAPI backend with MiKTeX Portable and Bun
# Multi-stage build for smaller final image

# =====================================================================
# Stage 1: Build dependencies and install Bun
# =====================================================================
# Pin to bookworm (Debian 12) because MiKTeX only provides an apt
# repository for bookworm. python:3.11-slim now defaults to trixie.
FROM python:3.11-slim-bookworm AS builder

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Bun
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"

# Install Python dependencies
COPY pyproject.toml .
COPY app/ ./app/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# =====================================================================
# Stage 2: Runtime image with MiKTeX installed via apt
# =====================================================================
# Pin to bookworm (Debian 12) to match the MiKTeX apt repository.
FROM python:3.11-slim-bookworm AS runtime

# Install runtime dependencies + MiKTeX
# MiKTeX's key.asc URL is no longer available (404), so we retrieve the
# signing key from the Ubuntu keyserver as documented by MiKTeX.
# Key ID: D6BC243565B2087BC3F897C9277A7293F59E4889
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    gnupg \
    ca-certificates \
    && gpg --homedir /tmp --no-default-keyring --keyring /usr/share/keyrings/miktex-keyring.gpg \
        --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys D6BC243565B2087BC3F897C9277A7293F59E4889 \
    && echo "deb [signed-by=/usr/share/keyrings/miktex-keyring.gpg] https://miktex.org/download/debian bookworm universe" > /etc/apt/sources.list.d/miktex.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends miktex \
    && rm -rf /var/lib/apt/lists/* \
    && miktexsetup --shared=yes finish \
    && initexmf --admin --set-config-value [MPM]AutoInstall=1 \
    && which lualatex && which xelatex && which pdfinfo && which pdftotext

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy Bun from builder (chown to appuser so cache is writable)
COPY --chown=appuser:appuser --from=builder /root/.bun /home/appuser/.bun
ENV PATH="/home/appuser/.bun/bin:${PATH}"

# Copy application code (only what's needed)
COPY --chown=appuser:appuser app/ ./app/

# Switch to non-root user for bun install (so node_modules is owned by appuser)
USER appuser

# Install scraper TypeScript dependencies
RUN for dir in app/external/scrapers/*/cli; do \
      if [ -f "$dir/package.json" ]; then \
        bun install --cwd "$dir"; \
      fi; \
    done

# Set MiKTeX binary path (installed via apt)
ENV LATEX_BIN_DIR=/usr/bin

# Switch back to root to install entrypoint
USER root

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch back to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check (only for API process)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run via entrypoint — set DOCKER_PROCESS=worker to start the ranking worker
CMD ["/entrypoint.sh"]