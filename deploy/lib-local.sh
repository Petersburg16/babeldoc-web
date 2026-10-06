# 本地脚本（deploy.sh、pull-backup.sh）共用，由它们 source，不单独运行。
# 这个文件会随版本一起上传，不要写主机信息：主机别名只放在命令行参数、BDW_DEPLOY_HOST 或 deploy/deploy.local（不入库）。

# 服务器上的安装目录，与 remote.sh 的 APP 一致
REMOTE_APP=/opt/babeldoc-web

# 依次取参数、环境变量 BDW_DEPLOY_HOST、deploy/deploy.local 里的 BDW_DEPLOY_HOST，结果放进 HOST；都没有就返回非零
resolve_host() {
  HOST="${1:-${BDW_DEPLOY_HOST:-}}"
  if [ -z "$HOST" ] && [ -f deploy/deploy.local ]; then
    # shellcheck disable=SC1091
    . deploy/deploy.local
    HOST="${BDW_DEPLOY_HOST:-}"
  fi
  [ -n "$HOST" ]
}
