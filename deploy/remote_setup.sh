#!/usr/bin/env bash
# 在目标 Ubuntu 主机上执行：安装依赖、部署到 /opt/ai-subtitle、注册 systemd 服务并自检
set -euo pipefail
APP_DIR=/opt/ai-subtitle
PORT="${PORT:-8000}"
SRC_TAR="${SRC_TAR:-/tmp/ai-subtitle.tar.gz}"
SVC=ai-subtitle
export DEBIAN_FRONTEND=noninteractive
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

step() { echo; echo "========== $* =========="; }

step "系统信息"
uname -a
. /etc/os-release && echo "$PRETTY_NAME"
python3 --version 2>/dev/null || echo "python3 未安装"

step "检查端口 $PORT"
if ss -ltnp 2>/dev/null | grep -E "[:.]$PORT\s" | grep -vq "$APP_DIR"; then
  if ! systemctl is-active --quiet "$SVC"; then
    echo "!! 端口 $PORT 已被其他程序占用："; ss -ltnp | grep -E "[:.]$PORT\s"; exit 3
  fi
fi
echo "端口可用"

step "安装系统依赖"
need=()
for p in python3 python3-venv curl ca-certificates; do
  dpkg -s "$p" >/dev/null 2>&1 || need+=("$p")
done
if ((${#need[@]})); then
  echo "需要安装: ${need[*]}"
  apt-get update
  apt-get install -y "${need[@]}"
else
  echo "系统依赖已齐全"
fi

step "解压项目到 $APP_DIR"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/app" "$APP_DIR/static" "$APP_DIR/tests"
tar --warning=no-unknown-keyword -xzf "$SRC_TAR" -C "$APP_DIR"
cd "$APP_DIR"
[ -f .env ] || cp .env.example .env   # 保留已有 .env
mkdir -p data                          # 模型配置与 API Key 保存在这里，重新部署不会覆盖
ls -la data | tail -n +2 | grep -v "^d" || true
[ -f data/auth.json ] && echo "保留已有的登录账户" || echo "尚未设置登录账户：首次打开页面时需要设置用户名和密码"
sed -i "s/^PORT=.*/PORT=$PORT/" .env
ls -la

step "创建虚拟环境并安装 Python 依赖"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import sys" 2>/dev/null; then
  rm -rf .venv; python3 -m venv .venv
fi
.venv/bin/python -m pip --version
pipi() {
  .venv/bin/pip install --disable-pip-version-check --timeout 30 "$@" \
    || .venv/bin/pip install --disable-pip-version-check --timeout 30 -i "$PIP_MIRROR" "$@"
}
pipi -q --upgrade pip
pipi -q -r requirements.txt
.venv/bin/python -c "import fastapi, uvicorn, httpx, pydantic; print('fastapi', fastapi.__version__, '| uvicorn', uvicorn.__version__, '| pydantic', pydantic.VERSION)"

step "运行项目自检"
.venv/bin/python tests/test_api.py

step "注册 systemd 服务"
id "$SVC" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC"
chown -R "$SVC:$SVC" "$APP_DIR"
chmod 600 .env
chmod 700 data
find data -maxdepth 1 -type f -exec chmod 600 {} +
cat > /etc/systemd/system/$SVC.service <<UNIT
[Unit]
Description=AI Subtitle Studio (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC
WorkingDirectory=$APP_DIR
EnvironmentFile=-$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp"
fi

step "本机接口测试"
for i in $(seq 1 30); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 1; done
echo -n "health: "; curl -fsS "http://127.0.0.1:$PORT/api/health"; echo
echo -n "未登录访问首页: "; curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" "http://127.0.0.1:$PORT/"
echo -n "未登录访问接口: "; curl -s -w " [%{http_code}]\n" "http://127.0.0.1:$PORT/api/settings"
echo -n "登录页: "; curl -fsS "http://127.0.0.1:$PORT/login.html" | grep -o "<title>[^<]*</title>"
echo -n "认证状态: "; curl -fsS "http://127.0.0.1:$PORT/api/auth/status"; echo
echo -n "管理员账户: "; runuser -u "$SVC" -- "$APP_DIR/.venv/bin/python" -m app.auth status
systemctl --no-pager --lines=5 status "$SVC" | head -12
echo "is-enabled: $(systemctl is-enabled $SVC)  is-active: $(systemctl is-active $SVC)"
rm -f "$SRC_TAR" /tmp/remote_setup.sh
echo; echo "REMOTE_SETUP_OK port=$PORT"
