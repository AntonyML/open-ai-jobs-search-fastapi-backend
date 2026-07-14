# Dockerfile for FastAPI backend with MiKTeX Portable and Bun
# Multi-stage build for smaller final image

# =====================================================================
# Stage 1: Build dependencies and install Bun
# =====================================================================
FROM python:3.11-slim AS builder

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Bun
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# =====================================================================
# Stage 2: Runtime image with MiKTeX installed via apt
# =====================================================================
FROM python:3.11-slim AS runtime

# Install runtime dependencies + MiKTeX
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    gnupg \
    && curl -fsSL https://miktex.org/download/key.asc | gpg --dearmor -o /usr/share/keyrings/miktex-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/miktex-keyring.gpg] https://miktex.org/download/debian bookworm universe" > /etc/apt/sources.list.d/miktex.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends miktex \
    && rm -rf /var/lib/apt/lists/* \
    && miktexsetup --shared=yes finish \
    && initexmf --admin --set-config-value [MPM]AutoInstall=1

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy Bun from builder
COPY --from=builder /root/.bun /home/appuser/.bun
ENV PATH="/home/appuser/.bun/bin:${PATH}"

# Copy application code
COPY --chown=appuser:appuser . .

# Set MiKTeX binary path (installed via apt)
ENV LATEX_BIN_DIR=/usr/bin

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run the application
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]