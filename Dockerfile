# Dockerfile for FastAPI backend with Typst
# PDFs (CV + cover letter) se compilan in-process con Typst - sin LaTeX ni Bun.

FROM python:3.11-slim-bookworm

# Runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies (includes typst)
COPY pyproject.toml README.md ./
COPY app/ ./app/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy entrypoint script
COPY --chown=appuser:appuser entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run via entrypoint (API server)
CMD ["/entrypoint.sh"]
