#!/usr/bin/env bash
# 从本机一键部署到远程 Ubuntu 主机：
#   deploy/deploy_to_host.sh                      # 使用 deploy/target.conf 里的默认目标
#   deploy/deploy_to_host.sh user@host [端口]      # 部署到其他主机
# 认证方式：DEPLOY_KEY=私钥路径（target.conf 里已设置），或 DEPLOY_PASS=密码。
# 登录用户不是 root 时，远程安装步骤通过 sudo 执行（需要免密 sudo）。日志写入 deploy/last-deploy.log
set -uo pipefail
cd "$(dirname "$0")/.."
ENV_KEY="${DEPLOY_KEY:-}"
[ -f deploy/target.conf ] && . deploy/target.conf
TARGET="${1:-${DEPLOY_TARGET:-}}"
[ -n "$TARGET" ] || { echo "用法: deploy_to_host.sh [user@host] [port]（或在 deploy/target.conf 里设置 DEPLOY_TARGET）"; exit 2; }
# 命令行指定的密钥优先；部署到其他主机时不沿用默认目标的密钥
if [ -n "$ENV_KEY" ]; then DEPLOY_KEY="$ENV_KEY"; elif [ "$TARGET" != "${DEPLOY_TARGET:-}" ]; then DEPLOY_KEY=""; fi
PORT="${2:-${DEPLOY_PORT:-8000}}"
HOST="${TARGET#*@}"
RUSER="${TARGET%@*}"; [ "$RUSER" = "$TARGET" ] && RUSER=root
LOG=deploy/last-deploy.log
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR)
if [ -n "${DEPLOY_KEY:-}" ]; then SSH_OPTS+=(-i "$DEPLOY_KEY" -o IdentitiesOnly=yes -o BatchMode=yes); fi
run() { if [ -n "${DEPLOY_PASS:-}" ] && [ -z "${DEPLOY_KEY:-}" ]; then deploy/remote.exp "$@"; else "$@"; fi; }
SUDO=""; [ "$RUSER" != root ] && SUDO="sudo -n"

{
  echo "### $(date '+%F %T') 部署到 $TARGET 端口 $PORT"
  TAR=$(mktemp -t ai-subtitle).tar.gz
  COPYFILE_DISABLE=1 tar --no-mac-metadata -czf "$TAR" \
    --exclude .git --exclude .venv --exclude .claude --exclude ./CLAUDE.md --exclude __pycache__ --exclude '*.pyc' --exclude .env \
    --exclude "deploy/*.log" --exclude "deploy/*.json" --exclude deploy/target.conf --exclude ./data --exclude .DS_Store .
  echo "打包完成: $(du -h "$TAR" | cut -f1)"

  run scp "${SSH_OPTS[@]}" "$TAR" "$TARGET:/tmp/ai-subtitle.tar.gz" || { echo "!! 上传失败"; exit 1; }
  run scp "${SSH_OPTS[@]}" deploy/remote_setup.sh "$TARGET:/tmp/remote_setup.sh" || { echo "!! 上传失败"; exit 1; }
  rm -f "$TAR"
  run ssh "${SSH_OPTS[@]}" "$TARGET" "$SUDO env PORT=$PORT bash /tmp/remote_setup.sh" || { echo "!! 远程部署失败"; exit 1; }

  echo; echo "========== 从本机访问 http://$HOST:$PORT =========="
  echo -n "health: "; curl -fsS -m 10 "http://$HOST:$PORT/api/health" && echo || { echo "!! 局域网访问失败"; exit 1; }
  echo -n "登录状态: "; curl -fsS -m 10 "http://$HOST:$PORT/api/auth/status" && echo
  code=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "http://$HOST:$PORT/api/settings")
  echo "未登录访问接口: $code"; [ "$code" = 401 ] || { echo "!! 未登录时接口没有被拦截"; exit 1; }
  echo "DEPLOY_DONE 浏览器打开 http://$HOST:$PORT"
} 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
