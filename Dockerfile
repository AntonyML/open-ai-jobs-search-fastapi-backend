# Dockerfile for FastAPI backend with MiKTeX Portable and Bun
# Multi-stage build for smaller final image

# =====================================================================
# Stage 1: Build dependencies and install MiKTeX Portable + Bun
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

# Install MiKTeX Portable
WORKDIR /app
RUN mkdir -p /app/app/external/latex/miktex-portable
# Download MiKTeX Portable installer
RUN curl -fsSL -o miktex-portable.exe "https://miktex.org/download/portable" \
    && chmod +x miktex-portable.exe \
    && ./miktex-portable.exe --extract-only --target=/app/app/external/latex/miktex-portable \
    && rm miktex-portable.exe

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# =====================================================================
# Stage 2: Runtime image
# =====================================================================
FROM python:3.11-slim AS runtime

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy MiKTeX Portable from builder
COPY --from=builder /app/app/external/latex/miktex-portable /app/app/external/latex/miktex-portable

# Copy Bun from builder
COPY --from=builder /root/.bun /home/appuser/.bun
ENV PATH="/home/appuser/.bun/bin:${PATH}"

# Copy application code
COPY --chown=appuser:appuser . .

# Set MiKTeX binary path
ENV LATEX_BIN_DIR=/app/app/external/latex/miktex-portable/miktex/bin/x64

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run the application
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]