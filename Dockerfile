# Dockerfile for FastAPI backend with Typst
# PDFs (CV + cover letter) se compilan in-process con Typst - sin LaTeX ni Bun.
# Python 3.12: el código usa backslashes dentro de f-strings (PEP 701),
# que no compilan en 3.11.

FROM python:3.12-slim-bookworm

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
# /app/logs, documents, generated etc. se crean en build para que el usuario
# no-root pueda escribir (logging, tracker, PDFs) — Render/Fly no montan disco.
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /app/logs /app/documents /app/generated /app/generated_cvs \
    && chown -R appuser:appuser /app

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
