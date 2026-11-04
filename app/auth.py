"""登录认证：首次使用时设置用户名和密码，之后所有页面和接口都需要登录。

- 凭据保存在 <数据目录>/auth.json（权限 600），密码用 PBKDF2-SHA256 加盐哈希，不保存明文。
- 登录后发一个签名的 HttpOnly Cookie；修改密码会更换签名密钥，所有设备都需要重新登录。
- 同一 IP 连续输错密码会被暂时锁定。
- 忘记密码：在服务器上执行 `python -m app.auth reset`，下次打开页面会重新要求设置。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from . import store

COOKIE = "ais_session"
ITERATIONS = 240_000
REMEMBER_SECONDS = 30 * 24 * 3600     # 勾选「保持登录」
SESSION_SECONDS = 12 * 3600           # 不勾选：关闭浏览器即失效，最长 12 小时
MAX_FAILS, FAIL_WINDOW, LOCK_SECONDS = 5, 300, 300

# 不需要登录就能访问的路径
PUBLIC_PATHS = {"/login.html", "/api/auth/status", "/api/auth/setup", "/api/auth/login", "/api/auth/logout", "/api/health", "/favicon.ico"}

_lock = threading.Lock()
_fails: Dict[str, List[float]] = {}


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400, retry_after: int = 0):
        super().__init__(message)
        self.message, self.status, self.retry_after = message, status, retry_after


# ---------------------------------------------------------------- 存储
def auth_file():
    return store.data_file().parent / "auth.json"


def _load() -> Optional[Dict[str, Any]]:
    f = auth_file()
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text("utf-8"))
        return d if d.get("username") and d.get("hash") and d.get("secret") else None
    except (OSError, ValueError):
        return None


def _save(d: Dict[str, Any]) -> None:
    f = auth_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump(d, fp)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, f)


def is_configured() -> bool:
    return _load() is not None


def _hash(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def _validate(username: str, password: str) -> None:
    if not (1 <= len(username) <= 32) or any(c.isspace() for c in username):
        raise AuthError("用户名需为 1–32 个字符，不能包含空格")
    if len(password) < 6:
        raise AuthError("密码至少 6 位")
    if len(password) > 128:
        raise AuthError("密码太长")


def _new_record(username: str, password: str) -> Dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {"username": username, "salt": salt.hex(), "iterations": ITERATIONS,
            "hash": _hash(password, salt), "secret": secrets.token_hex(32), "updated_at": int(time.time())}


# ---------------------------------------------------------------- 会话令牌
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(rec: Dict[str, Any], seconds: int) -> str:
    payload = json.dumps({"u": rec["username"], "e": int(time.time()) + seconds, "n": secrets.token_hex(6)}, separators=(",", ":")).encode()
    sig = hmac.new(bytes.fromhex(rec["secret"]), payload, hashlib.sha256).hexdigest()
    return f"{_b64(payload)}.{sig}"


def check_token(token: Optional[str]) -> Optional[str]:
    """有效则返回用户名。"""
    if not token or "." not in token:
        return None
    rec = _load()
    if not rec:
        return None
    body, sig = token.rsplit(".", 1)
    try:
        payload = _unb64(body)
    except (ValueError, TypeError):
        return None
    good = hmac.new(bytes.fromhex(rec["secret"]), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, sig):
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if data.get("u") != rec["username"] or int(data.get("e", 0)) < time.time():
        return None
    return rec["username"]


def set_cookie(resp, token: str, remember: bool) -> None:
    resp.set_cookie(COOKIE, token, max_age=REMEMBER_SECONDS if remember else None,
                    httponly=True, samesite="lax", path="/")


def clear_cookie(resp) -> None:
    resp.delete_cookie(COOKIE, path="/")


# ---------------------------------------------------------------- 操作
def _check_rate(ip: str) -> None:
    now = time.time()
    with _lock:
        recent = [t for t in _fails.get(ip, []) if now - t < max(FAIL_WINDOW, LOCK_SECONDS)]
        _fails[ip] = recent
        if len(recent) >= MAX_FAILS:
            wait = int(LOCK_SECONDS - (now - recent[-1])) + 1
            if wait > 0:
                raise AuthError(f"密码错误次数过多，请 {wait} 秒后再试", 429, wait)


def _record_fail(ip: str) -> None:
    with _lock:
        _fails.setdefault(ip, []).append(time.time())


def setup(username: str, password: str) -> Dict[str, Any]:
    username = (username or "").strip()
    _validate(username, password or "")
    with _lock:
        if _load():
            raise AuthError("已经设置过用户名和密码，请直接登录", 409)
        rec = _new_record(username, password)
        _save(rec)
    return rec


def login(username: str, password: str, ip: str) -> Dict[str, Any]:
    _check_rate(ip)
    rec = _load()
    if not rec:
        raise AuthError("尚未设置用户名和密码", 409)
    ok_user = hmac.compare_digest((username or "").strip(), rec["username"])
    ok_pass = hmac.compare_digest(_hash(password or "", bytes.fromhex(rec["salt"]), int(rec.get("iterations", ITERATIONS))), rec["hash"])
    if not (ok_user and ok_pass):
        _record_fail(ip)
        raise AuthError("用户名或密码错误", 401)
    with _lock:
        _fails.pop(ip, None)
    return rec


def change_password(current_user: str, old_password: str, new_password: str, new_username: str = "") -> Dict[str, Any]:
    rec = _load()
    if not rec or rec["username"] != current_user:
        raise AuthError("登录状态已失效，请重新登录", 401)
    if not hmac.compare_digest(_hash(old_password or "", bytes.fromhex(rec["salt"]), int(rec.get("iterations", ITERATIONS))), rec["hash"]):
        raise AuthError("当前密码不正确", 401)
    username = (new_username or rec["username"]).strip()
    _validate(username, new_password or "")
    with _lock:
        new = _new_record(username, new_password)   # 新的盐和签名密钥：其他设备的登录全部失效
        _save(new)
    return new


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ---------------------------------------------------------------- 中间件
class AuthMiddleware:
    """纯 ASGI 中间件（不影响接口里检测浏览器断开）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in PUBLIC_PATHS:
            return await self.app(scope, receive, send)
        req = Request(scope)
        user = check_token(req.cookies.get(COOKIE))
        if user:
            scope.setdefault("state", {})["user"] = user
            return await self.app(scope, receive, send)
        configured = is_configured()
        if path.startswith("/api/"):
            resp = JSONResponse(status_code=401, content={
                "error": "请先登录" if configured else "请先设置用户名和密码", "auth": "login" if configured else "setup"})
        else:
            nxt = "" if path in ("/", "/index.html") else "?next=" + quote(path)
            resp = RedirectResponse("/login.html" + nxt, status_code=302)
        await resp(scope, receive, send)


# ---------------------------------------------------------------- 命令行
def _cli(argv: List[str]) -> int:
    if len(argv) >= 2 and argv[1] == "reset":
        f = auth_file()
        if f.exists():
            f.unlink()
            print(f"已删除 {f}。下次打开页面时会要求重新设置用户名和密码。")
        else:
            print(f"{f} 不存在，当前未设置密码。")
        return 0
    if len(argv) >= 2 and argv[1] == "status":
        rec = _load()
        print(f"已设置，用户名：{rec['username']}" if rec else "未设置用户名和密码")
        return 0
    print("用法: python -m app.auth [status|reset]")
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
