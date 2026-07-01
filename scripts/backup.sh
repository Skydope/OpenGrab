#!/usr/bin/env bash
# OpenGrab — backup de DB (SQLite) con retención de 7 días.
#
# Uso:
#   ./scripts/backup.sh /ruta/a/downloads              # solo DB
#   ./scripts/backup.sh /ruta/a/downloads --include-downloads  # DB + archivos
#
# Requisitos: sqlite3.
#
# Los backups se guardan en <downloads>/backups/ con el formato
#   opengrab_YYYYMMDD.db
# Los backups de archivos (--include-downloads) se guardan en
#   opengrab_files_YYYYMMDD.tar.gz

set -euo pipefail

INCLUDE_DOWNLOADS=0
DOWNLOAD_DIR=""

for arg in "$@"; do
    case "$arg" in
        --include-downloads) INCLUDE_DOWNLOADS=1 ;;
        -h|--help)
            echo "OpenGrab backup"
            echo "  Usage: $0 <downloads-dir> [--include-downloads]"
            echo "  Keeps last 7 daily backups."
            exit 0
            ;;
        *) DOWNLOAD_DIR="$arg" ;;
    esac
done

if [ -z "$DOWNLOAD_DIR" ]; then
    echo "ERROR: specify the downloads directory path" >&2
    echo "  Usage: $0 <downloads-dir> [--include-downloads]" >&2
    exit 1
fi

if [ ! -d "$DOWNLOAD_DIR" ]; then
    echo "ERROR: $DOWNLOAD_DIR does not exist" >&2
    exit 1
fi

DB_FILE="$DOWNLOAD_DIR/opengrab.db"
BACKUP_DIR="$DOWNLOAD_DIR/backups"

if [ ! -f "$DB_FILE" ]; then
    echo "ERROR: $DB_FILE not found" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y%m%d)
BACKUP_DB="$BACKUP_DIR/opengrab_${DATE}.db"

echo "[opengrab] backup: DB → $BACKUP_DB"

if ! sqlite3 "$DB_FILE" ".backup $BACKUP_DB"; then
    echo "ERROR: backup failed" >&2
    exit 1
fi

# Descargas (opcional)
if [ "$INCLUDE_DOWNLOADS" -eq 1 ]; then
    BACKUP_FILES="$BACKUP_DIR/opengrab_files_${DATE}.tar.gz"
    echo "[opengrab] backup: files → $BACKUP_FILES"

    tar -czf "$BACKUP_FILES" \
        -C "$DOWNLOAD_DIR" \
        --exclude='backups' \
        --exclude='*.part' \
        --exclude='*.ytdl' \
        --exclude='opengrab_*' \
        --exclude='opengrab.db*' \
        --exclude='config.ini' \
        .
fi

# Retention: keep last 7 daily DB backups
KEEP=7
find "$BACKUP_DIR" -maxdepth 1 -name "opengrab_????????.db" 2>/dev/null \
    | sort -r \
    | tail -n +$((KEEP + 1)) \
    | while read -r old; do
        echo "[opengrab] pruning old backup: $(basename "$old")"
        rm -f "$old"
    done

echo "[opengrab] backup done"
