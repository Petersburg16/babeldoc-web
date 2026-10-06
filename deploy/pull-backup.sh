#!/usr/bin/env bash
# 把服务器上最新的备份拉回本机 backups/（已被 .gitignore 忽略；不含 secret.key，但有账号等私人数据）：
#   bash deploy/pull-backup.sh [ssh 主机别名]          主机默认取 BDW_DEPLOY_HOST，或 deploy/deploy.local
#   bash deploy/pull-backup.sh --new [ssh 主机别名]    先在服务器上做一次新备份再拉
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy/lib-local.sh
. deploy/lib-local.sh

fresh=""
if [ "${1:-}" = "--new" ]; then
  fresh=1
  shift
fi
resolve_host "${1:-}" || { echo "用法：bash deploy/pull-backup.sh [--new] <ssh 主机别名>" >&2; exit 2; }

REMOTE_SH="$REMOTE_APP/current/deploy/remote.sh"
if [ -n "$fresh" ]; then
  ssh "$HOST" bash "$REMOTE_SH" backup
fi
latest="$(ssh "$HOST" bash "$REMOTE_SH" latest-backup)"
[ -n "$latest" ] || { echo "服务器上还没有备份，可加 --new 先备份一次" >&2; exit 1; }

mkdir -p backups
chmod 700 backups 2>/dev/null || true
scp -q "$HOST:$latest" backups/
echo "已保存 backups/$(basename "$latest")"
