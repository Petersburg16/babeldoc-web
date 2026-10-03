#!/usr/bin/env bash
# 把服务器上最新的备份拉回本机 backups/（已被 .gitignore 忽略；不含 secret.key，但有账号等私人数据）：
#   bash deploy/pull-backup.sh [ssh 主机别名]          主机默认取 BDW_DEPLOY_HOST，或 deploy/deploy.local
#   bash deploy/pull-backup.sh --new [ssh 主机别名]    先在服务器上做一次新备份再拉
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fresh=""
if [ "${1:-}" = "--new" ]; then
  fresh=1
  shift
fi
HOST="${1:-${BDW_DEPLOY_HOST:-}}"
if [ -z "$HOST" ] && [ -f deploy/deploy.local ]; then
  # shellcheck disable=SC1091
  . deploy/deploy.local
  HOST="${BDW_DEPLOY_HOST:-}"
fi
[ -n "$HOST" ] || { echo "用法：bash deploy/pull-backup.sh [--new] <ssh 主机别名>" >&2; exit 2; }

if [ -n "$fresh" ]; then
  ssh "$HOST" bash /opt/babeldoc-web/current/deploy/remote.sh backup
fi
latest="$(ssh "$HOST" 'ls -1t /var/backups/babeldoc-web/babeldoc-web-*.tar.gz 2>/dev/null | head -1')"
[ -n "$latest" ] || { echo "服务器上还没有备份，可加 --new 先备份一次" >&2; exit 1; }

mkdir -p backups
chmod 700 backups 2>/dev/null || true
scp -q "$HOST:$latest" backups/
echo "已保存 backups/$(basename "$latest")"
