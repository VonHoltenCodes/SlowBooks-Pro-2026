# ============================================================================
# Slowbooks Pro 2026 — Docker Image
# Runs on Linux, macOS, and Windows via Docker Desktop
# ============================================================================

FROM python:3.13-slim AS base

# ---- Python 3.13 performance env ----
# Don't write .pyc at runtime (we pre-compile at build time below)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONHASHSEED=random \
    PYTHONMALLOC=pymalloc

# System dependencies for WeasyPrint (PDF generation) and PostgreSQL client (backup/restore)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libjpeg62-turbo \
    libpng16-16 \
    libxml2 \
    libxslt1.1 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-compile bytecode for every .py in the image (app + site-packages).
# At runtime PYTHONDONTWRITEBYTECODE=1 prevents re-writes, so this is pure startup win.
RUN python -m compileall -q -j 0 /usr/local/lib/python3.13/site-packages /app || true

RUN chmod +x docker-entrypoint.sh

RUN useradd -m -u 1000 slowbooks && chown -R slowbooks:slowbooks /app
USER slowbooks

EXPOSE 3001

ENTRYPOINT ["./docker-entrypoint.sh"]
