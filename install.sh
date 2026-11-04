#!/usr/bin/env bash
# 一键安装或升级 AI 字幕工作台（Ubuntu / Debian，需要 root）：
#   curl -fsSL https://raw.githubusercontent.com/bigdongdongCLUB/AI-subtitle/main/install.sh | sudo bash
# 指定端口（默认 8000，升级时沿用原来的端口）：
#   curl -fsSL https://raw.githubusercontent.com/bigdongdongCLUB/AI-subtitle/main/install.sh | sudo PORT=9000 bash
# 重复运行就是升级：data/ 和 .env 不会被覆盖。
set -euo pipefail

REPO=bigdongdongCLUB/AI-subtitle
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/ai-subtitle

die() { echo "!! $*" >&2; exit 1; }

# 整个流程放在函数里，末尾才调用：下载中断时不会执行半截脚本
main() {
  [ "$(id -u)" = 0 ] || die "需要 root 权限，请用 sudo 运行"
  command -v apt-get >/dev/null || die "只支持 Ubuntu / Debian（需要 apt-get）"
  command -v curl >/dev/null || die "请先安装 curl"
  command -v systemctl >/dev/null || die "需要 systemd"

  local port="${PORT:-}"
  [ -n "$port" ] || port="$(sed -n 's/^PORT=//p' "$APP_DIR/.env" 2>/dev/null | head -1)"
  port="${port:-8000}"
  case "$port" in ''|*[!0-9]*) die "端口不正确：$port" ;; esac

  local work
  work="$(mktemp -d)"
  trap 'rm -rf "${work:-}"' EXIT

  echo "下载 $REPO（$BRANCH）…"
  curl -fsSL --retry 3 --connect-timeout 15 "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" -o "$work/source.tar.gz" \
    || die "下载失败，请检查能否访问 github.com"
  mkdir "$work/src"
  tar -xzf "$work/source.tar.gz" -C "$work/src" --strip-components=1
  tar -czf "$work/package.tar.gz" -C "$work/src" .

  # 脚本可能来自管道：不让子进程读走标准输入
  env PORT="$port" SRC_TAR="$work/package.tar.gz" bash "$work/src/deploy/remote_setup.sh" </dev/null

  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo
  echo "安装完成：http://${ip:-服务器地址}:$port"
  echo "首次打开页面请立即设置用户名和密码：设置之前，能访问这个地址的人都可以抢先设置。"
}

main "$@"
