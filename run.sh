#!/usr/bin/env bash
# 初始化（如需）并启动沙漠驿站资料馆
set -e
cd "$(dirname "$0")"
DB="${1:-data/archive.db}"
PORT="${2:-8000}"
if [ ! -f "$DB" ]; then
  echo ">> 初始化数据库: $DB"
  python3 -m app.seed "$DB" > /dev/null
fi
exec python3 -m app.server "$PORT" "$DB"
