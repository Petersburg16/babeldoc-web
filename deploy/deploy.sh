#!/usr/bin/env bash
# 本地一键上线（Git Bash / macOS / Linux 均可），目标主机需能以 root 通过 ssh 登录：
#   bash deploy/deploy.sh <ssh 主机别名>
#   bash deploy/deploy.sh                 使用 BDW_DEPLOY_HOST，也可写在 deploy/deploy.local（不入库）
#   SKIP_BUILD=1 bash deploy/deploy.sh    跳过前端构建（dist 已是最新时）
# 打包的是当前工作区（git 跟踪 + 未忽略的新文件）再加上 frontend/dist，不要求先提交。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${1:-${BDW_DEPLOY_HOST:-}}"
if [ -z "$HOST" ] && [ -f deploy/deploy.local ]; then
  # shellcheck disable=SC1091
  . deploy/deploy.local
  HOST="${BDW_DEPLOY_HOST:-}"
fi
[ -n "$HOST" ] || { echo "用法：bash deploy/deploy.sh <ssh 主机别名>（或在 deploy/deploy.local 里写 BDW_DEPLOY_HOST=别名）" >&2; exit 2; }

rev="$(git rev-parse --short HEAD 2>/dev/null || echo init)"
[ -z "$(git status --porcelain)" ] || rev="$rev-dirty"
RELEASE="$(date +%y%m%d-%H%M%S)-$rev"

if [ -z "${SKIP_BUILD:-}" ]; then
  echo "==> 构建前端"
  (cd frontend && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)
fi
[ -f frontend/dist/index.html ] || { echo "frontend/dist 不存在，先构建前端" >&2; exit 1; }

LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
git ls-files --cached --others --exclude-standard >"$LIST"
find frontend/dist -type f >>"$LIST"

echo "==> 上传 $RELEASE → $HOST（$(wc -l <"$LIST" | tr -d ' ') 个文件）"
tar -czf - -T "$LIST" | ssh "$HOST" "set -e; d=/opt/babeldoc-web/releases/$RELEASE; mkdir -p \$d; tar -xzf - -C \$d --no-same-owner"

echo "==> 服务器安装"
ssh "$HOST" "bash /opt/babeldoc-web/releases/$RELEASE/deploy/remote.sh install $RELEASE"
