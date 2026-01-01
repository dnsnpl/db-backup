# 🗄️ Universal Database Backup Manager

Ein leichtgewichtiger Docker-Container für automatische Datenbank-Backups, konfiguriert über Container-Labels. Mit Prometheus-Metrics und optimiert für Komodo.

## ✨ Features

- **Label-basierte Konfiguration** - Einfach Labels an DB-Container hinzufügen
- **Multi-Datenbank Support** - PostgreSQL, MySQL, MariaDB, MongoDB, Redis, SQLite
- **Cron-Scheduling** - Flexible Zeitplanung per Cron-Syntax
- **Prometheus Metrics** - Vollständiges Monitoring inkl. Last Backup Status
- **Grafana Dashboard** - Fertiges Dashboard inklusive
- **Alert Rules** - Prometheus Alerts für Backup-Fehler
- **Automatische Retention** - Alte Backups werden automatisch gelöscht
- **Komprimierung** - gzip oder zstd
- **Ressourcenschonend** - Minimaler Memory/CPU Footprint
- **Komodo-optimiert** - Labels und Konfiguration für Komodo

## 🚀 Quick Start

### 1. Image bauen

```bash
docker build -t db-backup-manager:latest .
```

### 2. Stack deployen (Komodo)

```bash
docker compose up -d
```

### 3. Labels zu Datenbank-Containern hinzufügen

```yaml
services:
  postgres:
    image: postgres:16
    labels:
      - "db-backup.enable=true"
      - "db-backup.type=postgres"
      - "db-backup.schedule=0 2 * * *"
      - "db-backup.database=mydb"
      - "db-backup.user=postgres"
      - "db-backup.password=${POSTGRES_PASSWORD}"
      - "db-backup.retention=7"
```

## 📊 Prometheus Metrics

Der Container exposed Metrics auf Port `9090`:

```
http://db-backup:9090/metrics
```

### Verfügbare Metrics

| Metric | Beschreibung |
|--------|--------------|
| `db_backup_manager_up` | Manager läuft (1=up) |
| `db_backup_manager_uptime_seconds` | Uptime in Sekunden |
| `db_backup_containers_monitored` | Anzahl überwachter Container |
| `db_backup_last_success` | Letzter Backup-Status (1=ok, 0=fail, -1=pending) |
| `db_backup_last_timestamp_seconds` | Unix Timestamp des letzten Backups |
| `db_backup_last_duration_seconds` | Dauer des letzten Backups |
| `db_backup_last_size_bytes` | Größe des letzten Backups |
| `db_backup_next_scheduled_timestamp_seconds` | Nächstes geplantes Backup |
| `db_backup_seconds_until_next` | Sekunden bis zum nächsten Backup |
| `db_backup_seconds_since_last` | Sekunden seit letztem Backup |
| `db_backup_total` | Gesamtzahl Backup-Versuche |
| `db_backup_failures_total` | Gesamtzahl fehlgeschlagener Backups |

### Endpoints

| Endpoint | Beschreibung |
|----------|--------------|
| `/metrics` | Prometheus Metrics |
| `/status` | JSON Status-Übersicht |
| `/health` | Health Check |
| `/ready` | Readiness Check |

## 📈 Grafana Integration

1. Dashboard importieren: `grafana/dashboard.json`
2. Prometheus als Datasource konfigurieren

### Wichtige Panels

- **Manager Status** - Up/Down Status
- **Monitored Containers** - Anzahl überwachter DBs
- **Backup Status Table** - Übersicht aller Backups
- **Time Since Last Backup** - Graph mit Threshold-Linien
- **Backup Size/Duration** - Historische Daten

## 🚨 Alerting

Alert Rules in `prometheus/alerts.yml`:

| Alert | Severity | Beschreibung |
|-------|----------|--------------|
| `DbBackupManagerDown` | critical | Manager ist down |
| `DbBackupFailed` | critical | Backup fehlgeschlagen |
| `DbBackupOverdue` | critical | Kein Backup seit >25h |
| `DbBackupDelayed` | warning | Kein Backup seit >12h |
| `DbBackupSlow` | warning | Backup dauert >30min |
| `DbBackupLarge` | warning | Backup >10GB |

### Prometheus Konfiguration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'db-backup'
    static_configs:
      - targets: ['db-backup:9090']
    scrape_interval: 30s

rule_files:
  - '/etc/prometheus/alerts/db-backup.yml'
```

## 🗃️ Unterstützte Datenbanken

### PostgreSQL
```yaml
labels:
  - "db-backup.enable=true"
  - "db-backup.type=postgres"
  - "db-backup.database=mydb"  # oder "all"
  - "db-backup.user=postgres"
  - "db-backup.password=secret"
```

### MySQL / MariaDB
```yaml
labels:
  - "db-backup.enable=true"
  - "db-backup.type=mysql"  # oder "mariadb"
  - "db-backup.database=wordpress"
  - "db-backup.user=root"
  - "db-backup.password=secret"
  - "db-backup.extra-args=--single-transaction --quick"
```

### MongoDB
```yaml
labels:
  - "db-backup.enable=true"
  - "db-backup.type=mongodb"
  - "db-backup.database=all"
  - "db-backup.user=admin"
  - "db-backup.password=secret"
```

### Redis / Valkey
```yaml
labels:
  - "db-backup.enable=true"
  - "db-backup.type=redis"
  - "db-backup.password=secret"
```

### SQLite
```yaml
labels:
  - "db-backup.enable=true"
  - "db-backup.type=sqlite"
  - "db-backup.database=/data/db.sqlite3"
```

## 📋 Alle Labels

| Label | Pflicht | Default | Beschreibung |
|-------|---------|---------|--------------|
| `db-backup.enable` | ✅ | `false` | Backup aktivieren |
| `db-backup.type` | ✅ | - | Datenbanktyp |
| `db-backup.schedule` | ❌ | `0 2 * * *` | Cron Schedule |
| `db-backup.database` | ❌ | `all` | Datenbankname |
| `db-backup.user` | ❌ | - | DB Benutzer |
| `db-backup.password` | ❌ | - | DB Passwort |
| `db-backup.password-file` | ❌ | - | Pfad zur Passwort-Datei |
| `db-backup.host` | ❌ | Container | DB Host |
| `db-backup.port` | ❌ | Auto | DB Port |
| `db-backup.retention` | ❌ | `7` | Tage behalten |
| `db-backup.compression` | ❌ | `gzip` | gzip/zstd/none |
| `db-backup.extra-args` | ❌ | - | Extra Dump-Args |

## ⏰ Cron Schedule Beispiele

| Schedule | Beschreibung |
|----------|--------------|
| `0 2 * * *` | Täglich um 02:00 |
| `0 */6 * * *` | Alle 6 Stunden |
| `0 3 * * 0` | Sonntags um 03:00 |
| `30 4 1 * *` | Am 1. jeden Monats |
| `0 0 * * 1-5` | Werktags um Mitternacht |

## 🔧 Umgebungsvariablen

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `BACKUP_DIR` | `/backups` | Backup-Verzeichnis |
| `CHECK_INTERVAL` | `60` | Prüfintervall (Sekunden) |
| `METRICS_PORT` | `9090` | Prometheus Port |
| `LABEL_PREFIX` | `db-backup` | Label-Prefix |
| `TZ` | `UTC` | Zeitzone |

## 📁 Backup-Struktur

```
/backups/
├── postgres-main/
│   └── postgres/
│       ├── mydb_20240115_020000.sql.gz
│       └── mydb_20240116_020000.sql.gz
├── mysql-main/
│   └── mysql/
│       └── wordpress_20240117_030000.sql.gz
└── redis-cache/
    └── redis/
        └── dump_20240117_060000.rdb.gz
```

## 🛠️ CLI Commands

```bash
# Backup sofort ausführen
docker exec db-backup python backup_manager.py --run-now container-name

# Alle Konfigurationen anzeigen
docker exec db-backup python backup_manager.py --list

# Status via API
curl http://localhost:9099/status | jq
```

## 🔄 Restore

### PostgreSQL
```bash
gunzip -c backup.sql.gz | docker exec -i postgres psql -U user -d database
```

### MySQL/MariaDB
```bash
gunzip -c backup.sql.gz | docker exec -i mysql mysql -u root -p database
```

### MongoDB
```bash
tar -xf backup.tar && docker exec -i mongodb mongorestore --drop /path/to/dump
```

### Redis
```bash
docker cp backup.rdb redis:/data/dump.rdb
docker restart redis
```

## 📂 Projektstruktur

```
db-backup-container/
├── Dockerfile
├── docker-compose.yml        # Komodo-optimiert
├── requirements.txt
├── README.md
├── scripts/
│   └── backup_manager.py     # Hauptskript
├── examples/
│   ├── docker-compose.full-example.yml
│   ├── docker-compose.minimal.yml
│   └── backup-labels-reference.yml
├── grafana/
│   └── dashboard.json        # Grafana Dashboard
└── prometheus/
    └── alerts.yml            # Alert Rules
```

## ⚠️ Wichtige Hinweise

1. **Netzwerk**: Der Backup-Container muss die DB-Container erreichen können
2. **Berechtigungen**: Der DB-User braucht Leserechte
3. **Speicherplatz**: Genug Platz für Backups einplanen
4. **Testen**: Regelmäßig Restores testen!
5. **Docker Socket**: Nur Read-Only mounten (`:ro`)

## 🐛 Troubleshooting

### Backup läuft nicht
```bash
# Logs prüfen
docker logs db-backup

# Konfiguration prüfen
docker exec db-backup python backup_manager.py --list
```

### Container nicht gefunden
- Prüfen ob `db-backup.enable=true` gesetzt ist
- Prüfen ob Container im gleichen Netzwerk ist
- Labels auf Tippfehler prüfen

### Metrics nicht verfügbar
```bash
# Health Check
curl http://localhost:9099/health

# Metrics direkt abrufen
curl http://localhost:9099/metrics
```

## 📄 Lizenz

MIT License
