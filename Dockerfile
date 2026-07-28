# Dockerfile for FastAPI backend with Typst and Bun
# Multi-stage build for smaller final image

# =====================================================================
# Stage 1: Build dependencies and install Bun
# =====================================================================
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

# Install Python dependencies (including typst)
COPY pyproject.toml .
COPY app/ ./app/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .[typst]

# =====================================================================
# Stage 2: Runtime
# =====================================================================
FROM python:3.11-slim-bookworm AS runtime

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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

# Copy application code
COPY --chown=appuser:appuser app/ ./app/

# Switch to non-root user for bun install
USER appuser

# Install scraper TypeScript dependencies
RUN for dir in app/external/scrapers/*/cli; do \
      if [ -f "$dir/package.json" ]; then \
        bun install --cwd "$dir"; \
      fi; \
    done

# Switch back to root to install entrypoint
USER root

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch back to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run via entrypoint
CMD ["/entrypoint.sh"]