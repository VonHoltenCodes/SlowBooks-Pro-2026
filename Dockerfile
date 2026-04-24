# ============================================================================
# Slowbooks Pro 2026 — Docker Image
# Runs on Linux, macOS, and Windows via Docker Desktop
# ============================================================================

FROM python:3.12-slim AS base

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

RUN chmod +x docker-entrypoint.sh

EXPOSE 3001

RUN useradd -m -r slowbooks && chown -R slowbooks:slowbooks /app
USER slowbooks

ENTRYPOINT ["./docker-entrypoint.sh"]
