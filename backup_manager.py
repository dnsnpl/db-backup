#!/usr/bin/env python3
"""
Universal Database Backup Manager
Reads Docker labels from containers and performs scheduled backups.
Includes Prometheus metrics endpoint for monitoring.

Supported Labels:
  db-backup.enable=true              # Enable backup for this container
  db-backup.type=postgres|mysql|mariadb|mongodb|redis|sqlite
  db-backup.schedule=0 2 * * *       # Cron schedule (default: daily at 2am)
  db-backup.database=mydb            # Database name (or "all" for all databases)
  db-backup.user=username            # Database user
  db-backup.password=secret          # Database password (or use _FILE variant)
  db-backup.password-file=/path      # Path to password file
  db-backup.host=container_name      # Host (defaults to container name)
  db-backup.port=5432                # Port (defaults based on type)
  db-backup.retention=7              # Days to keep backups (default: 7)
  db-backup.compression=gzip|zstd|none  # Compression type (default: gzip)
  db-backup.extra-args=              # Extra arguments for dump command
"""

import os
import sys
import time
import json
import gzip
import shutil
import subprocess
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from http.server import HTTPServer, BaseHTTPRequestHandler
from croniter import croniter
import docker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Constants
BACKUP_DIR = Path(os.getenv('BACKUP_DIR', '/backups'))
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))  # seconds
METRICS_PORT = int(os.getenv('METRICS_PORT', '9090'))
LABEL_PREFIX = os.getenv('LABEL_PREFIX', 'db-backup')

# Default ports per database type
DEFAULT_PORTS = {
    'postgres': 5432,
    'postgresql': 5432,
    'mysql': 3306,
    'mariadb': 3306,
    'mongodb': 27017,
    'mongo': 27017,
    'redis': 6379,
    'sqlite': None,
}


# =============================================================================
# PROMETHEUS METRICS
# =============================================================================

class BackupMetrics:
    """Thread-safe storage for backup metrics."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._metrics: Dict[str, Dict[str, Any]] = {}
        self._start_time = time.time()
        self._containers_monitored = 0
    
    def set_containers_count(self, count: int):
        """Set the number of monitored containers."""
        self._containers_monitored = count
    
    def record_backup(self, container_name: str, db_type: str, database: str,
                      success: bool, duration_seconds: float, size_bytes: int,
                      next_run: Optional[datetime] = None):
        """Record metrics for a backup operation."""
        with self._lock:
            key = f"{container_name}_{database}"
            self._metrics[key] = {
                'container': container_name,
                'db_type': db_type,
                'database': database,
                'last_success': 1 if success else 0,
                'last_timestamp': time.time(),
                'last_duration_seconds': duration_seconds,
                'last_size_bytes': size_bytes,
                'next_scheduled': next_run.timestamp() if next_run else 0,
                'total_backups': self._metrics.get(key, {}).get('total_backups', 0) + 1,
                'total_failures': self._metrics.get(key, {}).get('total_failures', 0) + (0 if success else 1),
            }
    
    def update_schedule(self, container_name: str, database: str, next_run: Optional[datetime]):
        """Update the next scheduled run time."""
        with self._lock:
            key = f"{container_name}_{database}"
            if key not in self._metrics:
                self._metrics[key] = {
                    'container': container_name,
                    'db_type': 'unknown',
                    'database': database,
                    'last_success': -1,  # Never run
                    'last_timestamp': 0,
                    'last_duration_seconds': 0,
                    'last_size_bytes': 0,
                    'next_scheduled': next_run.timestamp() if next_run else 0,
                    'total_backups': 0,
                    'total_failures': 0,
                }
            else:
                self._metrics[key]['next_scheduled'] = next_run.timestamp() if next_run else 0
    
    def init_container(self, container_name: str, db_type: str, database: str, next_run: Optional[datetime]):
        """Initialize metrics for a container (before first backup)."""
        with self._lock:
            key = f"{container_name}_{database}"
            if key not in self._metrics:
                self._metrics[key] = {
                    'container': container_name,
                    'db_type': db_type,
                    'database': database,
                    'last_success': -1,  # -1 = never run yet
                    'last_timestamp': 0,
                    'last_duration_seconds': 0,
                    'last_size_bytes': 0,
                    'next_scheduled': next_run.timestamp() if next_run else 0,
                    'total_backups': 0,
                    'total_failures': 0,
                }
            else:
                self._metrics[key]['db_type'] = db_type
                self._metrics[key]['next_scheduled'] = next_run.timestamp() if next_run else 0
    
    def get_prometheus_metrics(self) -> str:
        """Generate Prometheus-format metrics."""
        lines = []
        
        # Manager metrics
        lines.append('# HELP db_backup_manager_up Whether the backup manager is running (1=up)')
        lines.append('# TYPE db_backup_manager_up gauge')
        lines.append('db_backup_manager_up 1')
        
        lines.append('# HELP db_backup_manager_uptime_seconds Uptime of the backup manager in seconds')
        lines.append('# TYPE db_backup_manager_uptime_seconds counter')
        lines.append(f'db_backup_manager_uptime_seconds {time.time() - self._start_time:.2f}')
        
        lines.append('# HELP db_backup_containers_monitored Number of containers being monitored for backups')
        lines.append('# TYPE db_backup_containers_monitored gauge')
        lines.append(f'db_backup_containers_monitored {self._containers_monitored}')
        
        # Per-backup metrics
        lines.append('')
        lines.append('# HELP db_backup_last_success Whether the last backup was successful (1=success, 0=failure, -1=never run)')
        lines.append('# TYPE db_backup_last_success gauge')
        
        lines.append('# HELP db_backup_last_timestamp_seconds Unix timestamp of the last backup attempt')
        lines.append('# TYPE db_backup_last_timestamp_seconds gauge')
        
        lines.append('# HELP db_backup_last_duration_seconds Duration of the last backup in seconds')
        lines.append('# TYPE db_backup_last_duration_seconds gauge')
        
        lines.append('# HELP db_backup_last_size_bytes Size of the last backup in bytes')
        lines.append('# TYPE db_backup_last_size_bytes gauge')
        
        lines.append('# HELP db_backup_next_scheduled_timestamp_seconds Unix timestamp of the next scheduled backup')
        lines.append('# TYPE db_backup_next_scheduled_timestamp_seconds gauge')
        
        lines.append('# HELP db_backup_seconds_until_next Seconds until next scheduled backup')
        lines.append('# TYPE db_backup_seconds_until_next gauge')
        
        lines.append('# HELP db_backup_seconds_since_last Seconds since last backup')
        lines.append('# TYPE db_backup_seconds_since_last gauge')
        
        lines.append('# HELP db_backup_total Total number of backup attempts')
        lines.append('# TYPE db_backup_total counter')
        
        lines.append('# HELP db_backup_failures_total Total number of failed backups')
        lines.append('# TYPE db_backup_failures_total counter')
        
        now = time.time()
        
        with self._lock:
            for key, m in self._metrics.items():
                labels = f'container="{m["container"]}",db_type="{m["db_type"]}",database="{m["database"]}"'
                
                lines.append(f'db_backup_last_success{{{labels}}} {m["last_success"]}')
                lines.append(f'db_backup_last_timestamp_seconds{{{labels}}} {m["last_timestamp"]:.0f}')
                lines.append(f'db_backup_last_duration_seconds{{{labels}}} {m["last_duration_seconds"]:.2f}')
                lines.append(f'db_backup_last_size_bytes{{{labels}}} {m["last_size_bytes"]}')
                lines.append(f'db_backup_next_scheduled_timestamp_seconds{{{labels}}} {m["next_scheduled"]:.0f}')
                
                # Calculate seconds until next backup
                if m["next_scheduled"] > 0:
                    seconds_until = max(0, m["next_scheduled"] - now)
                    lines.append(f'db_backup_seconds_until_next{{{labels}}} {seconds_until:.0f}')
                
                # Calculate seconds since last backup
                if m["last_timestamp"] > 0:
                    seconds_since = now - m["last_timestamp"]
                    lines.append(f'db_backup_seconds_since_last{{{labels}}} {seconds_since:.0f}')
                
                lines.append(f'db_backup_total{{{labels}}} {m["total_backups"]}')
                lines.append(f'db_backup_failures_total{{{labels}}} {m["total_failures"]}')
        
        return '\n'.join(lines) + '\n'
    
    def get_status_json(self) -> dict:
        """Get status as JSON for API endpoint."""
        with self._lock:
            return {
                'uptime_seconds': time.time() - self._start_time,
                'containers_monitored': self._containers_monitored,
                'backups': [
                    {
                        'container': m['container'],
                        'db_type': m['db_type'],
                        'database': m['database'],
                        'last_success': m['last_success'] == 1 if m['last_success'] >= 0 else None,
                        'last_backup': datetime.fromtimestamp(m['last_timestamp']).isoformat() if m['last_timestamp'] > 0 else None,
                        'next_backup': datetime.fromtimestamp(m['next_scheduled']).isoformat() if m['next_scheduled'] > 0 else None,
                        'last_duration_seconds': m['last_duration_seconds'],
                        'last_size_bytes': m['last_size_bytes'],
                        'total_backups': m['total_backups'],
                        'total_failures': m['total_failures'],
                    }
                    for m in self._metrics.values()
                ]
            }


# Global metrics instance
metrics = BackupMetrics()


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for Prometheus metrics and status endpoints."""
    
    def do_GET(self):
        if self.path == '/metrics':
            content = metrics.get_prometheus_metrics()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', len(content.encode()))
            self.end_headers()
            self.wfile.write(content.encode())
        
        elif self.path == '/status':
            content = json.dumps(metrics.get_status_json(), indent=2)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(content.encode()))
            self.end_headers()
            self.wfile.write(content.encode())
        
        elif self.path == '/health' or self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"healthy"}')
        
        elif self.path == '/ready' or self.path == '/readyz':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')
        
        else:
            # Root path - show available endpoints
            if self.path == '/':
                content = json.dumps({
                    'name': 'db-backup-manager',
                    'endpoints': {
                        '/metrics': 'Prometheus metrics',
                        '/status': 'JSON status overview',
                        '/health': 'Health check',
                        '/ready': 'Readiness check',
                    }
                }, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self.send_response(404)
                self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress default access logs
        pass


def start_metrics_server():
    """Start the Prometheus metrics HTTP server in a background thread."""
    server = HTTPServer(('0.0.0.0', METRICS_PORT), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Metrics server started on port {METRICS_PORT}")
    logger.info(f"  Prometheus: http://0.0.0.0:{METRICS_PORT}/metrics")
    logger.info(f"  Status:     http://0.0.0.0:{METRICS_PORT}/status")
    return server


# =============================================================================
# BACKUP CONFIGURATION
# =============================================================================

@dataclass
class BackupConfig:
    """Configuration for a database backup job."""
    container_id: str
    container_name: str
    db_type: str
    schedule: str = '0 2 * * *'
    database: str = 'all'
    user: Optional[str] = None
    password: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    retention_days: int = 7
    compression: str = 'gzip'
    extra_args: str = ''
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    
    def __post_init__(self):
        # Normalize db_type
        self.db_type = self.db_type.lower()
        if self.db_type == 'postgresql':
            self.db_type = 'postgres'
        if self.db_type == 'mongo':
            self.db_type = 'mongodb'
        
        # Set default port if not specified
        if self.port is None:
            self.port = DEFAULT_PORTS.get(self.db_type)
        
        # Set default host to container name
        if self.host is None:
            self.host = self.container_name
        
        # Calculate next run
        self._update_next_run()
    
    def _update_next_run(self):
        """Calculate the next run time based on cron schedule."""
        try:
            cron = croniter(self.schedule, datetime.now())
            self.next_run = cron.get_next(datetime)
        except Exception as e:
            logger.error(f"Invalid cron schedule '{self.schedule}': {e}")
            self.next_run = None


# =============================================================================
# BACKUP EXECUTOR
# =============================================================================

class BackupExecutor:
    """Executes database backups."""
    
    def __init__(self, config: BackupConfig):
        self.config = config
        self.backup_dir = BACKUP_DIR / config.container_name / config.db_type
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def execute(self) -> tuple[bool, float, int]:
        """Execute the backup and return (success, duration_seconds, size_bytes)."""
        start_time = time.time()
        
        logger.info(f"Starting backup for {self.config.container_name} ({self.config.db_type})")
        
        try:
            backup_file = self._run_backup(datetime.now().strftime('%Y%m%d_%H%M%S'))
            
            if backup_file and backup_file.exists():
                compressed_file = self._compress_backup(backup_file)
                final_file = compressed_file or backup_file
                size_bytes = final_file.stat().st_size if final_file.exists() else 0
                
                self._cleanup_old_backups()
                
                duration = time.time() - start_time
                logger.info(f"Backup completed: {final_file} ({size_bytes} bytes, {duration:.2f}s)")
                return True, duration, size_bytes
            else:
                duration = time.time() - start_time
                logger.error(f"Backup failed for {self.config.container_name}")
                return False, duration, 0
                
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Backup error for {self.config.container_name}: {e}")
            return False, duration, 0
    
    def _run_backup(self, timestamp: str) -> Optional[Path]:
        """Run the appropriate backup command based on database type."""
        db_type = self.config.db_type
        
        if db_type == 'postgres':
            return self._backup_postgres(timestamp)
        elif db_type in ('mysql', 'mariadb'):
            return self._backup_mysql(timestamp)
        elif db_type == 'mongodb':
            return self._backup_mongodb(timestamp)
        elif db_type == 'redis':
            return self._backup_redis(timestamp)
        elif db_type == 'sqlite':
            return self._backup_sqlite(timestamp)
        else:
            logger.error(f"Unsupported database type: {db_type}")
            return None
    
    def _backup_postgres(self, timestamp: str) -> Optional[Path]:
        """Backup PostgreSQL database."""
        backup_file = self.backup_dir / f"{self.config.database}_{timestamp}.sql"
        
        env = os.environ.copy()
        if self.config.password:
            env['PGPASSWORD'] = self.config.password
        
        if self.config.database == 'all':
            cmd = ['pg_dumpall']
            cmd.extend(['-h', self.config.host])
            cmd.extend(['-p', str(self.config.port)])
            if self.config.user:
                cmd.extend(['-U', self.config.user])
            backup_file = self.backup_dir / f"all_databases_{timestamp}.sql"
        else:
            cmd = ['pg_dump']
            cmd.extend(['-h', self.config.host])
            cmd.extend(['-p', str(self.config.port)])
            if self.config.user:
                cmd.extend(['-U', self.config.user])
            cmd.extend(['-d', self.config.database])
        
        if self.config.extra_args:
            cmd.extend(self.config.extra_args.split())
        
        cmd.extend(['-f', str(backup_file)])
        
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"pg_dump failed: {result.stderr}")
            return None
        
        return backup_file
    
    def _backup_mysql(self, timestamp: str) -> Optional[Path]:
        """Backup MySQL/MariaDB database."""
        backup_file = self.backup_dir / f"{self.config.database}_{timestamp}.sql"
        
        cmd = ['mysqldump']
        cmd.extend(['-h', self.config.host])
        cmd.extend(['-P', str(self.config.port)])
        
        if self.config.user:
            cmd.extend(['-u', self.config.user])
        
        if self.config.password:
            cmd.append(f'-p{self.config.password}')
        
        if self.config.database == 'all':
            cmd.append('--all-databases')
            backup_file = self.backup_dir / f"all_databases_{timestamp}.sql"
        else:
            cmd.append(self.config.database)
        
        if self.config.extra_args:
            cmd.extend(self.config.extra_args.split())
        
        with open(backup_file, 'w') as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
        
        if result.returncode != 0:
            logger.error(f"mysqldump failed: {result.stderr}")
            backup_file.unlink(missing_ok=True)
            return None
        
        return backup_file
    
    def _backup_mongodb(self, timestamp: str) -> Optional[Path]:
        """Backup MongoDB database."""
        backup_subdir = self.backup_dir / f"{self.config.database}_{timestamp}"
        
        cmd = ['mongodump']
        cmd.extend(['--host', self.config.host])
        cmd.extend(['--port', str(self.config.port)])
        
        if self.config.user:
            cmd.extend(['-u', self.config.user])
        
        if self.config.password:
            cmd.extend(['-p', self.config.password])
            cmd.append('--authenticationDatabase=admin')
        
        if self.config.database != 'all':
            cmd.extend(['-d', self.config.database])
        
        cmd.extend(['--out', str(backup_subdir)])
        
        if self.config.extra_args:
            cmd.extend(self.config.extra_args.split())
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"mongodump failed: {result.stderr}")
            return None
        
        # Create a tar archive of the dump directory
        archive_file = self.backup_dir / f"{self.config.database}_{timestamp}.tar"
        shutil.make_archive(str(archive_file.with_suffix('')), 'tar', backup_subdir)
        shutil.rmtree(backup_subdir)
        
        return archive_file
    
    def _backup_redis(self, timestamp: str) -> Optional[Path]:
        """Backup Redis database."""
        backup_file = self.backup_dir / f"dump_{timestamp}.rdb"
        
        cmd = ['redis-cli']
        cmd.extend(['-h', self.config.host])
        cmd.extend(['-p', str(self.config.port)])
        
        if self.config.password:
            cmd.extend(['-a', self.config.password])
            cmd.append('--no-auth-warning')
        
        # Trigger save
        save_cmd = cmd + ['BGSAVE']
        result = subprocess.run(save_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"redis BGSAVE failed: {result.stderr}")
            return None
        
        # Wait for save to complete
        time.sleep(2)
        
        # Copy the RDB file using redis-cli --rdb
        rdb_cmd = cmd + ['--rdb', str(backup_file)]
        result = subprocess.run(rdb_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"redis --rdb failed: {result.stderr}")
            return None
        
        return backup_file
    
    def _backup_sqlite(self, timestamp: str) -> Optional[Path]:
        """Backup SQLite database."""
        source_file = Path(self.config.database)
        
        if not source_file.exists():
            logger.error(f"SQLite database not found: {source_file}")
            return None
        
        backup_file = self.backup_dir / f"{source_file.stem}_{timestamp}.db"
        
        cmd = ['sqlite3', str(source_file), f'.backup {backup_file}']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"sqlite3 backup failed: {result.stderr}")
            return None
        
        return backup_file
    
    def _compress_backup(self, backup_file: Path) -> Optional[Path]:
        """Compress the backup file and return the compressed file path."""
        if self.config.compression == 'none':
            return backup_file
        
        if self.config.compression == 'gzip':
            compressed_file = backup_file.with_suffix(backup_file.suffix + '.gz')
            with open(backup_file, 'rb') as f_in:
                with gzip.open(compressed_file, 'wb', compresslevel=6) as f_out:
                    shutil.copyfileobj(f_in, f_out)
            backup_file.unlink()
            return compressed_file
        
        elif self.config.compression == 'zstd':
            compressed_file = backup_file.with_suffix(backup_file.suffix + '.zst')
            cmd = ['zstd', '-q', '--rm', str(backup_file), '-o', str(compressed_file)]
            subprocess.run(cmd, capture_output=True)
            return compressed_file
        
        return backup_file
    
    def _cleanup_old_backups(self):
        """Remove backups older than retention period."""
        if self.config.retention_days <= 0:
            return
        
        cutoff = datetime.now() - timedelta(days=self.config.retention_days)
        
        for backup_file in self.backup_dir.iterdir():
            if backup_file.is_file():
                file_time = datetime.fromtimestamp(backup_file.stat().st_mtime)
                if file_time < cutoff:
                    logger.info(f"Removing old backup: {backup_file}")
                    backup_file.unlink()


# =============================================================================
# BACKUP MANAGER
# =============================================================================

class BackupManager:
    """Manages database backup jobs by reading Docker container labels."""
    
    def __init__(self):
        self.docker_client = docker.from_env()
        self.configs: Dict[str, BackupConfig] = {}
    
    def scan_containers(self):
        """Scan all containers for backup labels."""
        containers = self.docker_client.containers.list()
        new_configs = {}
        
        for container in containers:
            labels = container.labels
            
            # Check if backup is enabled
            if labels.get(f'{LABEL_PREFIX}.enable', 'false').lower() != 'true':
                continue
            
            # Check if database type is specified
            db_type = labels.get(f'{LABEL_PREFIX}.type')
            if not db_type:
                logger.warning(f"Container {container.name} has backup enabled but no type specified")
                continue
            
            # Read password from file if specified
            password = labels.get(f'{LABEL_PREFIX}.password')
            password_file = labels.get(f'{LABEL_PREFIX}.password-file')
            if password_file and Path(password_file).exists():
                password = Path(password_file).read_text().strip()
            
            # Create config
            config = BackupConfig(
                container_id=container.id,
                container_name=container.name,
                db_type=db_type,
                schedule=labels.get(f'{LABEL_PREFIX}.schedule', '0 2 * * *'),
                database=labels.get(f'{LABEL_PREFIX}.database', 'all'),
                user=labels.get(f'{LABEL_PREFIX}.user'),
                password=password,
                host=labels.get(f'{LABEL_PREFIX}.host'),
                port=int(labels.get(f'{LABEL_PREFIX}.port', 0)) or None,
                retention_days=int(labels.get(f'{LABEL_PREFIX}.retention', 7)),
                compression=labels.get(f'{LABEL_PREFIX}.compression', 'gzip'),
                extra_args=labels.get(f'{LABEL_PREFIX}.extra-args', ''),
            )
            
            # Preserve last_run from existing config
            if container.id in self.configs:
                config.last_run = self.configs[container.id].last_run
                config._update_next_run()
            
            new_configs[container.id] = config
            
            # Initialize metrics for this container
            metrics.init_container(config.container_name, config.db_type, config.database, config.next_run)
            
            logger.debug(f"Found backup config for {container.name}: {db_type}")
        
        self.configs = new_configs
        metrics.set_containers_count(len(self.configs))
        logger.info(f"Found {len(self.configs)} containers with backup enabled")
    
    def check_and_run_backups(self):
        """Check scheduled backups and run if due."""
        now = datetime.now()
        
        for config in self.configs.values():
            if config.next_run and now >= config.next_run:
                logger.info(f"Running scheduled backup for {config.container_name}")
                
                executor = BackupExecutor(config)
                success, duration, size = executor.execute()
                
                config.last_run = now
                config._update_next_run()
                
                # Record metrics
                metrics.record_backup(
                    container_name=config.container_name,
                    db_type=config.db_type,
                    database=config.database,
                    success=success,
                    duration_seconds=duration,
                    size_bytes=size,
                    next_run=config.next_run
                )
                
                if success:
                    logger.info(f"Backup successful for {config.container_name}")
                else:
                    logger.error(f"Backup failed for {config.container_name}")
    
    def run_backup_now(self, container_name: str) -> bool:
        """Manually trigger a backup for a specific container."""
        for config in self.configs.values():
            if config.container_name == container_name:
                executor = BackupExecutor(config)
                success, duration, size = executor.execute()
                
                # Record metrics
                metrics.record_backup(
                    container_name=config.container_name,
                    db_type=config.db_type,
                    database=config.database,
                    success=success,
                    duration_seconds=duration,
                    size_bytes=size,
                    next_run=config.next_run
                )
                
                return success
        
        logger.error(f"Container not found or backup not enabled: {container_name}")
        return False
    
    def list_configs(self) -> List[Dict[str, Any]]:
        """List all backup configurations."""
        return [
            {
                'container': c.container_name,
                'type': c.db_type,
                'database': c.database,
                'schedule': c.schedule,
                'retention_days': c.retention_days,
                'next_run': c.next_run.isoformat() if c.next_run else None,
                'last_run': c.last_run.isoformat() if c.last_run else None,
            }
            for c in self.configs.values()
        ]
    
    def run_forever(self):
        """Run the backup manager loop."""
        logger.info("=" * 60)
        logger.info("Database Backup Manager Starting")
        logger.info("=" * 60)
        logger.info(f"Backup directory: {BACKUP_DIR}")
        logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
        logger.info(f"Label prefix: {LABEL_PREFIX}")
        logger.info(f"Metrics port: {METRICS_PORT}")
        logger.info("=" * 60)
        
        # Start metrics server
        start_metrics_server()
        
        while True:
            try:
                self.scan_containers()
                self.check_and_run_backups()
            except Exception as e:
                logger.error(f"Error in backup loop: {e}")
            
            time.sleep(CHECK_INTERVAL)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Database Backup Manager')
    parser.add_argument('--run-now', metavar='CONTAINER', help='Run backup now for container')
    parser.add_argument('--list', action='store_true', help='List all backup configurations')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon (default)')
    
    args = parser.parse_args()
    
    manager = BackupManager()
    manager.scan_containers()
    
    if args.list:
        configs = manager.list_configs()
        print(json.dumps(configs, indent=2, default=str))
    elif args.run_now:
        success = manager.run_backup_now(args.run_now)
        sys.exit(0 if success else 1)
    else:
        manager.run_forever()


if __name__ == '__main__':
    main()
