#!/usr/bin/env bash
# 服务器端运维脚本（以 root 运行）。由本地 deploy.sh 上传版本后调用，也可手动使用：
#   bash /opt/babeldoc-web/current/deploy/remote.sh install <release>   安装并切换到指定版本
#   bash /opt/babeldoc-web/current/deploy/remote.sh rollback            回到上一个版本
#   bash /opt/babeldoc-web/current/deploy/remote.sh status | logs
#   bash /opt/babeldoc-web/current/deploy/remote.sh cli create-admin <用户名>   （需 ssh -t）
set -euo pipefail

APP=/opt/babeldoc-web
DATA=/var/lib/babeldoc-web
SVC=babeldoc-web
RUN_USER=babeldoc
PORT=8090
KEEP=5

UV="$(command -v uv || true)"
[ -n "$UV" ] || UV=/root/.local/bin/uv
export UV_PYTHON_INSTALL_DIR="$APP/python"
export UV_CACHE_DIR="$APP/uv-cache"
export UV_PYTHON_PREFERENCE=only-managed

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

ensure_base() {
  local rel="$1"
  id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$RUN_USER"
  install -d -o "$RUN_USER" -g "$RUN_USER" -m 750 "$DATA"
  install -d -m 755 "$APP" "$APP/releases" "$APP/shared"
  if [ ! -f "$APP/shared/env" ]; then
    install -m 640 -g "$RUN_USER" "$rel/deploy/env.example" "$APP/shared/env"
    log "已生成 $APP/shared/env（按需修改）"
  fi
}

engine_mode() {
  grep -E '^BDW_ENGINE=' "$APP/shared/env" | tail -1 | cut -d= -f2 | tr -d '"' || true
}

warmup_assets() {
  local rel="$1"
  [ "$(engine_mode)" = "babeldoc" ] || return 0
  local version marker
  version="$("$rel/engine/.venv/bin/python" -c 'import importlib.metadata as m; print(m.version("babeldoc"))')"
  marker="$DATA/.cache/babeldoc/.warmup-$version"
  [ -f "$marker" ] && return 0
  if [ ! -d "$DATA/.cache/babeldoc/models" ] && [ -d /root/.cache/babeldoc/models ]; then
    log "复制已有的 BabelDOC 模型与字体缓存"
    install -d -o "$RUN_USER" -g "$RUN_USER" "$DATA/.cache"
    cp -a /root/.cache/babeldoc "$DATA/.cache/"
    chown -R "$RUN_USER:$RUN_USER" "$DATA/.cache/babeldoc"
  fi
  log "校验/下载 BabelDOC $version 离线资产（首次约 340 MB）"
  runuser -u "$RUN_USER" -- env HOME="$DATA" "$rel/engine/.venv/bin/python" -c \
    'import babeldoc.assets.assets as a; a.warmup()' >/dev/null
  runuser -u "$RUN_USER" -- touch "$marker"
}

health() {
  for _ in $(seq 1 30); do
    curl -fsS -m 3 "http://127.0.0.1:$PORT/api/meta" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

switch_to() {
  ln -sfn "$1" "$APP/current.next"
  mv -Tf "$APP/current.next" "$APP/current"
}

install_release() {
  local name="${1:?缺少版本名}"
  local rel="$APP/releases/$name"
  [ -d "$rel" ] || die "找不到 $rel"
  ensure_base "$rel"

  log "同步后端依赖"
  (cd "$rel/backend" && "$UV" sync --frozen --no-dev --compile-bytecode -q)
  log "同步引擎依赖（babeldoc 版本见 engine/pyproject.toml）"
  (cd "$rel/engine" && "$UV" sync --frozen --compile-bytecode -q)
  "$rel/backend/.venv/bin/python" -m compileall -q "$rel/backend/app" "$rel/engine" >/dev/null || true
  chmod -R a+rX "$APP/python" "$rel"
  [ -f "$rel/frontend/dist/index.html" ] || die "版本里缺少 frontend/dist，前端没有构建"

  warmup_assets "$rel"

  if ! cmp -s "$rel/deploy/babeldoc-web.service" "/etc/systemd/system/$SVC.service"; then
    log "更新 systemd 单元"
    install -m 644 "$rel/deploy/babeldoc-web.service" "/etc/systemd/system/$SVC.service"
    systemctl daemon-reload
  fi
  systemctl enable "$SVC" >/dev/null 2>&1 || true

  local previous=""
  [ -L "$APP/current" ] && previous="$(readlink -f "$APP/current")"
  log "切换到 $name 并重启服务"
  switch_to "$rel"
  systemctl restart "$SVC"
  if ! health; then
    journalctl -u "$SVC" -n 40 --no-pager || true
    if [ -n "$previous" ] && [ "$previous" != "$rel" ]; then
      log "健康检查失败，回滚到 $(basename "$previous")"
      switch_to "$previous"
      systemctl restart "$SVC"
    fi
    die "新版本没有通过健康检查"
  fi
  log "服务正常：http://127.0.0.1:$PORT"

  local old
  old="$(ls -1dt "$APP"/releases/*/ | tail -n +$((KEEP + 1)))"
  if [ -n "$old" ]; then
    while read -r dir; do
      [ "$(readlink -f "$dir")" = "$(readlink -f "$APP/current")" ] || rm -rf "$dir"
    done <<<"$old"
  fi

  if ! runuser -u "$RUN_USER" -- test -s "$DATA/app.db" || [ "$(cli_count_users)" = "0" ]; then
    log "还没有管理员，请运行：ssh -t <主机> bash $APP/current/deploy/remote.sh cli create-admin <用户名>"
  fi
}

cli_count_users() {
  runuser -u "$RUN_USER" -- env HOME="$DATA" BDW_DATA_DIR="$DATA" "$APP/current/backend/.venv/bin/python" -c \
    'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("select count(*) from users").fetchone()[0])' \
    "$DATA/app.db" 2>/dev/null || echo 0
}

rollback() {
  local current previous
  current="$(readlink -f "$APP/current")"
  previous="$(ls -1dt "$APP"/releases/*/ | sed 's:/$::' | grep -vx "$current" | head -1 || true)"
  [ -n "$previous" ] || die "没有可回滚的版本"
  log "回滚到 $(basename "$previous")"
  switch_to "$previous"
  systemctl restart "$SVC"
  health && log "服务正常" || die "回滚后服务未就绪，查看：journalctl -u $SVC -n 100"
}

run_cli() {
  set -a
  # shellcheck disable=SC1091
  . "$APP/shared/env"
  set +a
  cd "$APP/current/backend"
  exec runuser -u "$RUN_USER" -- env HOME="$DATA" BDW_ENGINE="${BDW_ENGINE:-babeldoc}" BDW_DATA_DIR="$DATA" \
    "$APP/current/backend/.venv/bin/python" -m app.cli "$@"
}

case "${1:-}" in
  install) install_release "${2:-}" ;;
  rollback) rollback ;;
  status)
    systemctl --no-pager status "$SVC" | head -12
    echo "current -> $(readlink -f "$APP/current")"
    curl -fsS -m 3 "http://127.0.0.1:$PORT/api/meta" | head -c 300 && echo
    ;;
  logs) journalctl -u "$SVC" -n "${2:-200}" --no-pager ;;
  cli) shift && run_cli "$@" ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
