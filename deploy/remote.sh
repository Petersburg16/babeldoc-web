#!/usr/bin/env bash
# 服务器端运维脚本（以 root 运行）。由本地 deploy.sh 上传版本后调用，也可手动使用：
#   bash /opt/babeldoc-web/current/deploy/remote.sh install <release>   安装并切换到指定版本
#   bash /opt/babeldoc-web/current/deploy/remote.sh rollback            回到上一个版本
#   bash /opt/babeldoc-web/current/deploy/remote.sh status | logs
#   bash /opt/babeldoc-web/current/deploy/remote.sh backup | backups    立即备份 / 列出备份（每天也会自动备份）
#   bash /opt/babeldoc-web/current/deploy/remote.sh restore <备份> [--yes]   校验备份；加 --yes 才真正恢复
#   bash /opt/babeldoc-web/current/deploy/remote.sh cli create-admin <用户名>   （需 ssh -t）
set -euo pipefail

APP=/opt/babeldoc-web
DATA=/var/lib/babeldoc-web
SVC=babeldoc-web
RUN_USER=babeldoc
PORT=8090
KEEP=5
BACKUP_DIR=/var/backups/babeldoc-web
BACKUP_KEEP=14

UV="$(command -v uv || true)"
[ -n "$UV" ] || UV=/root/.local/bin/uv
export UV_PYTHON_INSTALL_DIR="$APP/python"
export UV_CACHE_DIR="$APP/uv-cache"
export UV_PYTHON_PREFERENCE=only-managed

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
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

sync_pdf_assets() {
  local rel="$1"
  [ -f "$rel/frontend/pdf-assets.lock.json" ] || return 0
  log "同步 PDF 工具资源（只在版本变化时下载，中文字体优先复用 BabelDOC 的缓存）"
  install -d -o "$RUN_USER" -g "$RUN_USER" -m 755 "$DATA/pdf-assets"
  runuser -u "$RUN_USER" -- env HOME="$DATA" BDW_DATA_DIR="$DATA" "$rel/backend/.venv/bin/python" -m app.cli \
    pdf-assets sync --lock "$rel/frontend/pdf-assets.lock.json" --dest "$DATA/pdf-assets" \
    --seed "$DATA/.cache/babeldoc/fonts" --keep 2 \
    || die "PDF 工具资源同步失败，未切换版本"
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
  # 会议记录用 ffmpeg 转换录音格式；缺了只影响会议记录，不拦部署
  if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
    warn "没找到 ffmpeg / ffprobe，会议记录无法处理录音（apt install ffmpeg）"
  fi

  log "同步后端依赖"
  (cd "$rel/backend" && "$UV" sync --frozen --no-dev --compile-bytecode -q)
  log "同步引擎依赖（babeldoc 版本见 engine/pyproject.toml）"
  (cd "$rel/engine" && "$UV" sync --frozen --compile-bytecode -q)
  "$rel/backend/.venv/bin/python" -m compileall -q "$rel/backend/app" "$rel/engine" >/dev/null || true
  chmod -R a+rX "$APP/python" "$rel"
  [ -f "$rel/frontend/dist/index.html" ] || die "版本里缺少 frontend/dist，前端没有构建"

  warmup_assets "$rel"
  (cd "$rel/backend" && sync_pdf_assets "$rel")

  local unit changed=""
  for unit in "$SVC.service" "$SVC-backup.service" "$SVC-backup.timer"; do
    if ! cmp -s "$rel/deploy/$unit" "/etc/systemd/system/$unit"; then
      install -m 644 "$rel/deploy/$unit" "/etc/systemd/system/$unit"
      changed=1
    fi
  done
  if [ -n "$changed" ]; then
    log "更新 systemd 单元"
    systemctl daemon-reload
  fi
  systemctl enable "$SVC" >/dev/null 2>&1 || true
  systemctl enable --now "$SVC-backup.timer" >/dev/null 2>&1 || true

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

# 快照与校验都以服务用户运行：以 root 打开 app.db 会让 WAL 的 -shm/-wal 归 root，服务就写不进去了
DB_TOOL='
import sqlite3, sys
mode, db = sys.argv[1], sys.argv[2]
if mode == "snapshot":
    src, conn = sqlite3.connect(db), sqlite3.connect(sys.argv[3])
    src.backup(conn)  # 在线备份 API：服务运行中也能得到一致快照，直接 cp 可能拿到半截事务
    src.close()
    conn.execute("pragma journal_mode=delete")
else:
    conn = sqlite3.connect(db)
check = conn.execute("pragma integrity_check").fetchone()[0]
tables = {r[0] for r in conn.execute("select name from sqlite_master where type = 'table'")}
wanted = ("users", "model_profiles", "jobs", "meetings", "asr_providers", "meeting_llm_models", "meeting_llm_presets")
counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in wanted if t in tables}
note = ""
if mode == "verify" and len(sys.argv) > 3:
    # 备份不含 secret.key：看本机现有的密钥能解开多少个模型 Key
    from cryptography.fernet import Fernet, InvalidToken
    fernet = Fernet(open(sys.argv[3], "rb").read().strip())
    tokens = [r[0] for r in conn.execute("select api_key_enc from model_profiles where length(api_key_enc) > 0")]
    if "asr_providers" in tables:  # 语音识别服务的密钥同样用 secret.key 加密
        tokens += [r[0] for r in conn.execute("select secret_enc from asr_providers where length(secret_enc) > 0")]
    if "meeting_llm_models" in tables:  # 会议用的大模型 Key
        tokens += [r[0] for r in conn.execute("select api_key_enc from meeting_llm_models where length(api_key_enc) > 0")]
    usable = 0
    for token in tokens:
        try:
            fernet.decrypt(token.encode())
            usable += 1
        except InvalidToken:
            pass
    note = f" keys={usable}/{len(tokens)}"
    if usable < len(tokens):
        note += f"（{len(tokens) - usable} 个密钥用本机 secret.key 解不开，恢复后要在后台重新填写）"
conn.close()
print(check, " ".join(f"{k}={v}" for k, v in counts.items()) + note)
sys.exit(0 if check == "ok" else 1)
'

db_tool() {
  runuser -u "$RUN_USER" -- env HOME="$DATA" "$APP/current/backend/.venv/bin/python" -c "$DB_TOOL" "$@"
}

backup() {
  [ -s "$DATA/app.db" ] || die "找不到 $DATA/app.db"
  install -d -m 700 "$BACKUP_DIR"
  local tmp summary out
  tmp="$(runuser -u "$RUN_USER" -- mktemp -d)"
  summary="$(db_tool snapshot "$DATA/app.db" "$tmp/app.db")" || { rm -rf "$tmp"; die "数据库快照校验失败：$summary"; }
  # 不放 secret.key：备份会被拉到别处保存，带上它就等于带上了能解出模型 API Key 的钥匙；丢了只需在后台重填 Key
  cp -p "$APP/shared/env" "$tmp/env"
  out="$BACKUP_DIR/$SVC-$(date +%Y%m%d-%H%M%S).tar.gz"
  (umask 077 && tar -C "$tmp" -czf "$out" app.db env)
  rm -rf "$tmp"
  ls -1t "$BACKUP_DIR/$SVC"-*.tar.gz | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f
  log "已备份 $out（$(du -h "$out" | cut -f1)；$summary）"
}

list_backups() {
  ls -lh "$BACKUP_DIR/$SVC"-*.tar.gz 2>/dev/null || echo "还没有备份"
  systemctl list-timers "$SVC-backup.timer" --no-pager 2>/dev/null | sed -n '1,2p'
}

restore() {
  local archive="${1:-}" confirm="${2:-}"
  [ -n "$archive" ] || die "用法：remote.sh restore <备份文件> [--yes]（不加 --yes 只校验）"
  [ -f "$archive" ] || archive="$BACKUP_DIR/$archive"
  [ -f "$archive" ] || die "找不到备份：$1"
  local tmp summary
  tmp="$(runuser -u "$RUN_USER" -- mktemp -d)"
  tar -xzf "$archive" -C "$tmp" app.db
  chown "$RUN_USER:$RUN_USER" "$tmp/app.db"
  summary="$(db_tool verify "$tmp/app.db" "$DATA/secret.key")" || { rm -rf "$tmp"; die "备份里的数据库校验失败：$summary"; }
  log "备份可用：$(basename "$archive")（$summary）"
  if [ "$confirm" != "--yes" ]; then
    rm -rf "$tmp"
    log "只做了校验，没有改动任何数据；确认恢复请加 --yes（会先自动备份当前数据）"
    return 0
  fi
  log "恢复前先备份当前数据"
  backup
  log "停止服务，替换数据库（secret.key 保持不动）"
  systemctl stop "$SVC"
  rm -f "$DATA/app.db-wal" "$DATA/app.db-shm"
  install -o "$RUN_USER" -g "$RUN_USER" -m 644 "$tmp/app.db" "$DATA/app.db"
  rm -rf "$tmp"
  systemctl start "$SVC"
  health && log "已恢复，服务正常" || die "恢复后服务未就绪，查看：journalctl -u $SVC -n 100"
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
  backup) backup ;;
  backups) list_backups ;;
  restore) restore "${2:-}" "${3:-}" ;;
  cli) shift && run_cli "$@" ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
