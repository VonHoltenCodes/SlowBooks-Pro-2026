# ============================================================================
# SlowBooks Pro 2026 — Docker Image
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
# tesseract-ocr + poppler-utils: receipt intake (Tier 2 OCR) — per the design doc,
# Docker/native installs add the system package; never bundled into the signed installers.
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
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-compile bytecode for every .py in the image (app + site-packages).
# At runtime PYTHONDONTWRITEBYTECODE=1 prevents re-writes, so this is pure startup win.
RUN python -m compileall -q -j 0 /usr/local/lib/python3.13/site-packages /app || true

RUN chmod +x docker-entrypoint.sh

# The folders docker compose mounts volumes on (backups, and the uploads
# folder releases before 2.18 wrote) exist here, owned by the app's user:
# Docker gives an empty named volume the owner of the image's folder, and a
# folder the image lacks becomes a volume owned by root, which the app can't
# write. Every backup failed with "Permission denied", and so did every
# upload before 2.18. Those failures left an existing install's volumes
# empty, so they take the right owner when the new image starts.
RUN useradd -m -u 1000 slowbooks \
    && mkdir -p /app/backups /app/app/static/uploads \
    && chown -R slowbooks:slowbooks /app
USER slowbooks

# The commit this image was built from, shown on About as <version>+<commit>
# (#816). The build context has no .git, so the compose files pass it in:
#   SLOWBOOKS_COMMIT=$(git rev-parse HEAD) docker compose up -d --build
# Left empty, About shows the bare version. Declared last so a new commit
# rebuilds only this layer.
ARG SLOWBOOKS_COMMIT=
ENV SLOWBOOKS_COMMIT=${SLOWBOOKS_COMMIT}

EXPOSE 3001

# Liveness probe — hits the unauthenticated /health endpoint via a stdlib
# urllib script (no curl in the image). The script does NOT follow redirects:
# with FORCE_HTTPS=true /health answers http with a 307 to https, and chasing
# that to a non-TLS port would flap the container unhealthy while the worker
# is fine — so a 2xx or 3xx both count as alive. Marks unhealthy after
# 3 consecutive failures (90s): short enough for orchestrators to restart
# promptly, long enough to ride out a single GC pause or migration replay.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "scripts/docker_healthcheck.py"]

ENTRYPOINT ["./docker-entrypoint.sh"]
