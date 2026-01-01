# =============================================================================
# Universal Database Backup Container
# Supports: PostgreSQL, MySQL/MariaDB, MongoDB, Redis, SQLite
# =============================================================================
FROM python:3.12-slim

# Metadata
LABEL maintainer="db-backup-manager"
LABEL description="Universal database backup container with label-based configuration"
LABEL org.opencontainers.image.title="DB Backup Manager"
LABEL org.opencontainers.image.description="Automatic database backups via Docker labels"
LABEL org.opencontainers.image.version="1.0.0"

# Komodo Labels
LABEL komodo.stack.category="Infrastructure"
LABEL komodo.stack.description="Database Backup Manager - Automatic backups via labels"

# Install database clients and tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    # PostgreSQL client
    postgresql-client \
    # MySQL/MariaDB client
    default-mysql-client \
    # MongoDB tools - will install separately
    gnupg curl wget \
    # Redis tools
    redis-tools \
    # SQLite
    sqlite3 \
    # Compression tools
    gzip \
    zstd \
    # Utilities
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install MongoDB tools (separate because they need special repo)
RUN curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | gpg --dearmor -o /usr/share/keyrings/mongodb-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/mongodb-archive-keyring.gpg] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mongodb-database-tools \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application
COPY scripts/ /app/

# Create backup directory with proper permissions
RUN mkdir -p /backups && chmod 755 /backups

# Create non-root user (optional, uncomment if needed)
# RUN useradd -r -s /bin/false backupuser && chown -R backupuser:backupuser /app /backups
# USER backupuser

# Set working directory
WORKDIR /app

# Environment variables with sensible defaults
ENV BACKUP_DIR=/backups
ENV CHECK_INTERVAL=60
ENV METRICS_PORT=9090
ENV LABEL_PREFIX=db-backup
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

# Expose metrics port
EXPOSE 9090

# Health check via HTTP endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:9090/health || exit 1

# Run the backup manager
ENTRYPOINT ["python", "backup_manager.py"]
CMD ["--daemon"]
