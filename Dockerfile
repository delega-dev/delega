FROM python:3.11-slim

WORKDIR /app

# Install backend deps first for better layer caching
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy app source
COPY backend /app/backend
COPY frontend /app/frontend
COPY README.md /app/README.md
COPY LICENSE /app/LICENSE

# Run migration on startup (idempotent)
COPY backend/migrations /app/backend/migrations

# Runtime defaults (can be overridden by env vars)
# Supports both DELEGA_* and legacy FLUX_* for backward compat
ENV DELEGA_HOST=0.0.0.0 \
    DELEGA_PORT=18890 \
    DELEGA_DB_PATH=/app/data/delega.db

VOLUME /app/data

EXPOSE 18890

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:18890/health')" || exit 1

# Start server: migrations run via main.py on first start (SQLAlchemy creates tables)
# Then 001_add_agents.py adds agent-specific schema additions
CMD ["sh", "-c", "cd /app && python -c 'import sys; sys.path.insert(0,\"/app\"); from backend.database import engine; from backend.models import Base; Base.metadata.create_all(bind=engine)' && python /app/backend/migrations/001_add_agents.py && python /app/backend/main.py"]
