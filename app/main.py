"""AI 字幕翻译润色工具 —— FastAPI 入口。"""
import asyncio
import hashlib
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, store, thinking
from .llm import LLMError, list_models, log, translate_items
from .subtitles import build_subtitle, parse_subtitle

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="AI Subtitle Studio", version="1.0.0")
app.add_middleware(auth.AuthMiddleware)   # 所有页面和接口都需要登录（登录页和认证接口除外）


@app.exception_handler(LLMError)
async def llm_error_handler(_, exc: LLMError):
    return JSONResponse(status_code=exc.status, content={"error": exc.message, "detail": exc.detail})


# ---------- 数据模型 ----------
class Provider(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3
    timeout: float = 120
    json_mode: bool = False
    max_tokens: Optional[int] = None
    thinking: str = "off"  # 思考深度：off / on / low / medium / high / max / default（不指定），见 app/thinking.py
    profile_id: str = ""   # api_key 为空时，用服务器上该配置保存的 Key


class Task(BaseModel):
    mode: str = "translate"  # translate | polish | translate_polish
    source_lang: str = "自动检测"
    target_lang: str = "中文（简体）"
    tone: str = "natural"
    instructions: str = ""
    glossary: str = ""
    single_line: bool = True  # 要求模型每条输出一行（前端再按宽度换行）
    skip_lyrics: bool = False  # 歌词保留原文（整条是歌词/音效的由前端直接跳过）


class Item(BaseModel):
    id: int
    text: str


class Context(BaseModel):
    before: List[str] = Field(default_factory=list)
    after: List[str] = Field(default_factory=list)


class TranslateRequest(BaseModel):
    provider: Provider = Field(default_factory=Provider)
    task: Task = Field(default_factory=Task)
    items: List[Item]
    context: Context = Field(default_factory=Context)


class ModelsRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    profile_id: str = ""


class ThinkingQuery(BaseModel):
    base_url: str = ""
    model: str = ""
    current: Optional[str] = None


class ProfilesSave(BaseModel):
    base_version: int
    activeId: str = ""
    list: List[Dict[str, Any]]


class ActiveSave(BaseModel):
    activeId: str


class TaskSave(BaseModel):
    task: Dict[str, Any]


class Credentials(BaseModel):
    username: str = ""
    password: str = ""
    remember: bool = True


class PasswordChange(BaseModel):
    old_password: str
    new_password: str
    new_username: str = ""


class ProjectPut(BaseModel):
    project: Dict[str, Any]


class ProjectPatch(BaseModel):
    project_id: str
    cues: List[Dict[str, Any]]


def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/")


def fill_stored_key(p: Dict[str, Any]) -> Dict[str, Any]:
    """浏览器不持有 Key：api_key 为空且带了 profile_id 时，从服务器存储里取 Key。
    为防止 Key 被发往其他地址，只有请求的 API 地址与该配置保存的地址一致时才使用。"""
    if p.get("api_key") or not p.get("profile_id"):
        return p
    saved = store.get_profile(p["profile_id"])
    if not saved or not saved.get("api_key"):
        return p
    if _norm_url(p.get("base_url")) != _norm_url(saved.get("base_url")):
        raise HTTPException(400, "API 地址与已保存的不一致：修改了地址后请重新输入 API Key，或先保存配置")
    return {**p, "api_key": saved["api_key"]}


class ExportRequest(BaseModel):
    cues: List[Dict[str, Any]]
    format: str = "srt"
    content: str = "translation"  # translation | original | bilingual_to | bilingual_ot
    header: str = ""
    filename: str = "subtitle"
    font_size: int = 44         # ASS 字号（按 play_res 画布计）
    secondary_scale: int = 75   # ASS 双语：原文相对大小（%）
    play_res: int = 1080        # ASS 画面高度：720 / 1080 / 2160
    style: Optional[Dict[str, Any]] = None  # ASS 主/副字幕字体和颜色：font, color, outline_color, font2, color2, outline_color2


# ---------- 接口 ----------
@app.get("/api/config")
async def get_config():
    """告诉前端服务器端是否预置了模型配置（不返回密钥本身）。"""
    return {
        "server_base_url": os.getenv("LLM_BASE_URL", ""),
        "server_model": os.getenv("LLM_MODEL", ""),
        "server_has_key": bool(os.getenv("LLM_API_KEY")),
    }


@app.post("/api/parse")
async def api_parse(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件过大（>20MB）")
    try:
        return parse_subtitle(data, file.filename or "")
    except Exception as e:  # pysubs2 解析失败
        raise HTTPException(400, f"无法解析字幕文件: {e}")


@app.post("/api/export")
async def api_export(req: ExportRequest):
    try:
        text, ext = build_subtitle(req.cues, req.format, req.content, req.header, req.font_size, req.secondary_scale, req.play_res, req.style)
    except ValueError as e:
        raise HTTPException(400, str(e))
    name = (req.filename or "subtitle").rsplit(".", 1)[0] + "." + ext
    quoted = urllib.parse.quote(name)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@app.post("/api/models")
async def api_models(req: ModelsRequest):
    return {"models": await list_models(fill_stored_key(req.dict()))}


@app.post("/api/thinking/options")
async def api_thinking_options(req: ThinkingQuery):
    """按服务商和模型返回可选的思考深度档位。"""
    return thinking.options(req.base_url, req.model, req.current)


# ---------- 设置（保存在服务器，所有浏览器共用） ----------
@app.get("/api/settings")
async def api_get_settings():
    return store.get_public()


@app.put("/api/settings/profiles")
async def api_save_profiles(req: ProfilesSave):
    try:
        return store.save_profiles(req.base_version, req.activeId, req.list)
    except store.StoreConflict:
        return JSONResponse(status_code=409, content={"error": "模型设置已在其他设备上被修改", "current": store.get_public()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/settings/active")
async def api_set_active(req: ActiveSave):
    try:
        return store.set_active(req.activeId)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.put("/api/settings/task")
async def api_save_task(req: TaskSave):
    return store.save_task(req.task)


@app.post("/api/translate")
async def api_translate(req: TranslateRequest, request: Request):
    if not req.items:
        raise HTTPException(400, "items 为空")
    if req.task.mode not in ("translate", "polish", "translate_polish"):
        raise HTTPException(400, f"不支持的模式: {req.task.mode}")
    if len(req.items) > 200:
        raise HTTPException(400, "单次最多 200 条")
    job = asyncio.ensure_future(translate_items(
        fill_stored_key(req.provider.dict()),
        [it.dict() for it in req.items],
        req.task.dict(),
        req.context.dict(),
    ))
    # 浏览器点了「停止」或关闭页面时，取消正在进行的模型调用，避免继续消耗额度
    while True:
        done, _ = await asyncio.wait({job}, timeout=1.0)
        if done:
            return job.result()
        if await request.is_disconnected():
            job.cancel()
            log.info("LLM 浏览器已断开，取消批次 #%s-%s", req.items[0].id, req.items[-1].id)
            return JSONResponse(status_code=499, content={"error": "客户端已取消"})


# ---------- 登录认证 ----------
def _auth_error(e: auth.AuthError) -> JSONResponse:
    headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
    return JSONResponse(status_code=e.status, content={"error": e.message}, headers=headers)


@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    user = auth.check_token(request.cookies.get(auth.COOKIE))
    return {"configured": auth.is_configured(), "authenticated": bool(user), "username": user or ""}


@app.post("/api/auth/setup")
async def api_auth_setup(req: Credentials):
    try:
        rec = auth.setup(req.username, req.password)
    except auth.AuthError as e:
        return _auth_error(e)
    log.info("AUTH 已设置管理员用户名：%s", rec["username"])
    resp = JSONResponse({"ok": True, "username": rec["username"]})
    auth.set_cookie(resp, auth.make_token(rec, auth.REMEMBER_SECONDS if req.remember else auth.SESSION_SECONDS), req.remember)
    return resp


@app.post("/api/auth/login")
async def api_auth_login(req: Credentials, request: Request):
    ip = auth.client_ip(request)
    try:
        rec = auth.login(req.username, req.password, ip)
    except auth.AuthError as e:
        if e.status in (401, 429):
            log.warning("AUTH 登录失败 ip=%s user=%s：%s", ip, req.username[:32], e.message)
        return _auth_error(e)
    resp = JSONResponse({"ok": True, "username": rec["username"]})
    auth.set_cookie(resp, auth.make_token(rec, auth.REMEMBER_SECONDS if req.remember else auth.SESSION_SECONDS), req.remember)
    return resp


@app.post("/api/auth/logout")
async def api_auth_logout():
    resp = JSONResponse({"ok": True})
    auth.clear_cookie(resp)
    return resp


@app.post("/api/auth/password")
async def api_auth_password(req: PasswordChange, request: Request):
    user = request.scope.get("state", {}).get("user", "")
    try:
        rec = auth.change_password(user, req.old_password, req.new_password, req.new_username)
    except auth.AuthError as e:
        return _auth_error(e)
    log.info("AUTH 已修改密码，用户名：%s（其他设备需重新登录）", rec["username"])
    resp = JSONResponse({"ok": True, "username": rec["username"]})
    auth.set_cookie(resp, auth.make_token(rec, auth.REMEMBER_SECONDS), True)
    return resp


# ---------- 字幕项目（保存在服务器） ----------
@app.get("/api/project")
async def api_project_get(since: int = 0, pid: str = ""):
    return store.project_get(since, pid)


@app.get("/api/project/version")
async def api_project_version():
    return store.project_version()


@app.put("/api/project")
async def api_project_put(req: ProjectPut):
    try:
        return store.project_replace(req.project)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/api/project")
async def api_project_patch(req: ProjectPatch):
    try:
        return store.project_patch(req.project_id, req.cues)
    except store.StoreConflict:
        return JSONResponse(status_code=409, content={"error": "其他设备已打开了另一个字幕文件", **store.project_version()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/project")
async def api_project_delete():
    store.project_delete()
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"ok": True}


# ---------- 前端页面与静态文件 ----------
# 部署后浏览器必须拿到新的脚本：首页里的 app.js / reflow.js / style.css 带上内容版本号，
# 文件一变地址就变；所有静态文件都要求浏览器每次向服务器确认（no-cache，没变时返回 304）。
_ASSET_RE = re.compile(r'(src|href)="(app\.js|reflow\.js|style\.css)"')


def _asset_version(name: str) -> str:
    try:
        return hashlib.md5((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def index_page():
    html = (STATIC_DIR / "index.html").read_text("utf-8")
    html = _ASSET_RE.sub(lambda m: f'{m.group(1)}="{m.group(2)}?v={_asset_version(m.group(2))}"', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


# 放在最后，避免覆盖 /api 路由
app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
