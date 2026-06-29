# ── Urban ABM — Backend ───────────────────────────────────────────────────────
FROM python:3.11-slim

# geo packages (shapely 2.x, fiona 1.9.x, pyproj 3.x) ship manylinux wheels
# that bundle their native libs (GEOS, GDAL, PROJ).  gcc + libspatialindex are
# the only system-level extras still required.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libspatialindex-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source + bundled spatial databases
COPY Backend/ ./Backend/

# Override Windows-style paths and localhost binding
ENV DATABASE_PATH=/app/Backend/Environment/eixample_overture.duckdb
ENV HOST=0.0.0.0
ENV PORT=8000
ENV RELOAD=false

WORKDIR /app/Backend/Agent
EXPOSE 8000

CMD ["uvicorn", "map_server:app", "--host", "0.0.0.0", "--port", "8000"]
