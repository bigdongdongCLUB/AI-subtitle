"""服务器端设置存储：模型配置（含 API Key）与任务设置，保存在一个 JSON 文件里。

- 文件默认在 <项目目录>/data/settings.json，可用环境变量 DATA_DIR 修改。
- API Key 只保存在服务器上，接口返回给浏览器的配置里不含明文 Key，只有遮挡后的提示。
- 写入是原子的（先写临时文件再改名），文件权限 600。
"""
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent

PROFILE_FIELDS = ("id", "name", "preset", "base_url", "model", "temperature", "timeout", "json_mode", "thinking")
TASK_FIELDS = ("mode", "source_lang", "target_lang", "tone", "glossary", "instructions", "batch_size", "concurrency",
               "line_merge", "line_width", "font_size", "font_scale2", "skip_lyrics", "export_res", "ass_style",
               "export_format", "export_content", "ass_import_style")
MAX_PROFILES = 50

_lock = threading.Lock()
_data_dir: Optional[Path] = None


class StoreConflict(Exception):
    """客户端基于旧版本修改，服务器上的配置已被其他设备更新。"""


def configure(data_dir: Optional[str] = None) -> None:
    """测试或自定义部署时指定数据目录。"""
    global _data_dir
    _data_dir = Path(data_dir) if data_dir else None


def data_file() -> Path:
    d = _data_dir or Path(os.getenv("DATA_DIR") or (BASE_DIR / "data"))
    return d / "settings.json"


def _empty() -> Dict[str, Any]:
    return {"profiles": {"version": 0, "activeId": "", "list": []}, "task": {"version": 0, "value": {}}}


def _read() -> Dict[str, Any]:
    f = data_file()
    if not f.exists():
        return _empty()
    try:
        data = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        # 文件损坏时保留一份备份，避免直接覆盖丢失
        try:
            f.replace(f.with_suffix(f".broken-{int(time.time())}.json"))
        except OSError:
            pass
        return _empty()
    base = _empty()
    base["profiles"].update(data.get("profiles") or {})
    base["task"].update(data.get("task") or {})
    return base


def _write(data: Dict[str, Any]) -> None:
    f = data_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(f.parent, 0o700)
    except OSError:
        pass
    tmp = f.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, f)


def key_hint(key: str) -> str:
    key = key or ""
    if not key:
        return ""
    if len(key) <= 10:
        return "已设置"
    return f"{key[:5]}…{key[-4:]}"


def _public_profile(p: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: p.get(k) for k in PROFILE_FIELDS}
    out["has_key"] = bool(p.get("api_key"))
    out["key_hint"] = key_hint(p.get("api_key", ""))
    return out


def _public(data: Dict[str, Any]) -> Dict[str, Any]:
    pr = data["profiles"]
    return {
        "profiles": {"version": pr["version"], "activeId": pr["activeId"], "list": [_public_profile(p) for p in pr["list"]]},
        "task": {"version": data["task"]["version"], "value": data["task"]["value"]},
        "storage": str(data_file()),
    }


def get_public() -> Dict[str, Any]:
    with _lock:
        return _public(_read())


def _clean_profile(p: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["id"] = str(p.get("id") or "").strip()[:64]
    out["name"] = str(p.get("name") or "").strip()[:80]
    out["preset"] = str(p.get("preset") or "custom")[:40]
    out["base_url"] = str(p.get("base_url") or "").strip().rstrip("/")[:500]
    out["model"] = str(p.get("model") or "").strip()[:200]
    try:
        out["temperature"] = max(0.0, min(2.0, float(p.get("temperature", 0.3))))
    except (TypeError, ValueError):
        out["temperature"] = 0.3
    try:
        out["timeout"] = max(10, min(1800, int(float(p.get("timeout", 120)))))
    except (TypeError, ValueError):
        out["timeout"] = 120
    out["json_mode"] = bool(p.get("json_mode"))
    out["thinking"] = p.get("thinking") if p.get("thinking") in ("off", "on", "low", "medium", "high", "max", "default") else "off"
    return out


def save_profiles(base_version: int, active_id: str, incoming: List[Dict[str, Any]]) -> Dict[str, Any]:
    """保存完整的配置列表。

    每个配置里 api_key 的含义：
      - 字段不存在或为 null：保留服务器上该配置原来的 Key
      - 字符串（可为空字符串）：设置为新 Key（空字符串表示清除）
      - copy_key_from：新配置从另一个已保存配置复制 Key（用于「复制」功能）
    """
    if not incoming:
        raise ValueError("至少需要保留一个配置")
    if len(incoming) > MAX_PROFILES:
        raise ValueError(f"最多保存 {MAX_PROFILES} 个配置")
    with _lock:
        data = _read()
        cur = data["profiles"]
        if int(base_version) != int(cur["version"]):
            raise StoreConflict()
        old_by_id = {p["id"]: p for p in cur["list"]}
        new_list: List[Dict[str, Any]] = []
        seen = set()
        for raw in incoming:
            p = _clean_profile(raw)
            if not p["id"] or p["id"] in seen:
                raise ValueError("配置 id 无效或重复")
            seen.add(p["id"])
            # 已保存的 Key 只沿用到同一个 API 地址；地址改了必须重新填写，防止 Key 被发往其他服务器
            src = old_by_id.get(raw.get("copy_key_from") or "")
            old = old_by_id.get(p["id"])
            if raw.get("api_key") is not None:
                key = str(raw["api_key"]).strip()
            elif src and src.get("base_url") == p["base_url"]:
                key = src.get("api_key", "")
            elif old and old.get("base_url") == p["base_url"]:
                key = old.get("api_key", "")
            else:
                key = ""
            p["api_key"] = key
            new_list.append(p)
        ids = [p["id"] for p in new_list]
        cur["list"] = new_list
        cur["activeId"] = active_id if active_id in ids else ids[0]
        cur["version"] = int(cur["version"]) + 1
        cur["updated_at"] = int(time.time())
        _write(data)
        return _public(data)


def set_active(active_id: str) -> Dict[str, Any]:
    with _lock:
        data = _read()
        cur = data["profiles"]
        if active_id not in [p["id"] for p in cur["list"]]:
            raise ValueError("配置不存在")
        if cur["activeId"] != active_id:
            cur["activeId"] = active_id
            cur["version"] = int(cur["version"]) + 1
            cur["updated_at"] = int(time.time())
            _write(data)
        return _public(data)


def save_task(value: Dict[str, Any]) -> Dict[str, Any]:
    clean = {k: value[k] for k in TASK_FIELDS if k in value}
    for k in ("glossary", "instructions"):
        if k in clean:
            clean[k] = str(clean[k])[:20000]
    for k in ("line_merge", "skip_lyrics"):
        if k in clean:
            clean[k] = bool(clean[k])
    if "ass_style" in clean or "ass_import_style" in clean:
        from .subtitles import clean_style
        for key in ("ass_style", "ass_import_style"):
            if key in clean:
                raw = clean[key] if isinstance(clean[key], dict) else {}
                style = clean_style(raw)
                clean[key] = style if key == "ass_style" else {k: v for k, v in style.items() if k in raw}
    if "font_size" in clean:
        try:
            # 以 1080p 计：4K 的 10 和 720p 的 240 对应 5 和 360。
            clean["font_size"] = min(360, max(5, float(clean["font_size"])))
        except (TypeError, ValueError):
            clean["font_size"] = 44
    if "export_res" in clean:
        try:
            clean["export_res"] = int(clean["export_res"]) if int(clean["export_res"]) in (720, 1080, 2160) else 1080
        except (TypeError, ValueError):
            clean["export_res"] = 1080
    for k, allowed in (("export_format", ("srt", "vtt", "ass")),
                       ("export_content", ("translation", "bilingual_to", "bilingual_ot", "original"))):
        if k in clean and clean[k] not in allowed:
            del clean[k]   # 不合法或尚未选择：保留原来的值
    for k, lo, hi, default in (("line_width", 16, 120, 50), ("font_scale2", 40, 100, 75)):
        if k in clean:
            try:
                clean[k] = max(lo, min(hi, int(float(clean[k]))))
            except (TypeError, ValueError):
                clean[k] = default
    with _lock:
        data = _read()
        data["task"]["value"] = {**data["task"]["value"], **clean}
        data["task"]["version"] = int(data["task"]["version"]) + 1
        _write(data)
        return {"version": data["task"]["version"], "value": data["task"]["value"]}


def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    """内部使用：返回含 Key 的配置。"""
    if not profile_id:
        return None
    with _lock:
        for p in _read()["profiles"]["list"]:
            if p["id"] == profile_id:
                return dict(p)
    return None


# ================================================================ 字幕项目
# 当前正在编辑的字幕保存在 <数据目录>/project.json。
# 每条字幕带一个 _v（最后修改时的版本号），浏览器只上传改动的条目、只下载别人改动的条目，
# 两台电脑同时改不同的行不会互相覆盖。
CUE_FIELDS = ("id", "start", "end", "text", "translation", "status", "meta", "raw")
PROJECT_META = ("filename", "format", "header")
MAX_CUES = 50000

_project_cache: Optional[Dict[str, Any]] = None
_project_cache_path: Optional[Path] = None


def project_file() -> Path:
    return data_file().parent / "project.json"


def _project_load() -> Optional[Dict[str, Any]]:
    global _project_cache, _project_cache_path
    f = project_file()
    if _project_cache_path == f:
        return _project_cache
    data = None
    if f.exists():
        try:
            data = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            try:
                f.replace(f.with_suffix(f".broken-{int(time.time())}.json"))
            except OSError:
                pass
            data = None
    _project_cache, _project_cache_path = data, f
    return data


def _project_write(data: Optional[Dict[str, Any]]) -> None:
    global _project_cache, _project_cache_path
    f = project_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        if f.exists():
            f.unlink()
    else:
        tmp = f.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, separators=(",", ":"))
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, f)
    _project_cache, _project_cache_path = data, f


def _clean_cue(c: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: c[k] for k in CUE_FIELDS if k in c}
    try:
        out["id"] = int(out["id"])
        out["start"] = max(0, int(out.get("start", 0)))
        out["end"] = max(0, int(out.get("end", 0)))
    except (KeyError, TypeError, ValueError):
        raise ValueError("字幕条目格式错误")
    out["text"] = str(out.get("text") or "")
    out["translation"] = str(out.get("translation") or "")
    st = out.get("status") or ""
    out["status"] = st if st in ("done", "error", "edited") else ""
    return out


def project_get(since: int = 0, project_id: str = "") -> Dict[str, Any]:
    with _lock:
        p = _project_load()
        if not p:
            return {"version": 0, "project": None}
        if since and project_id == p["project_id"]:
            changed = [c for c in p["cues"] if c.get("_v", 0) > since]
            return {"version": p["version"], "project_id": p["project_id"], "full": False, "changed": changed}
        return {"version": p["version"], "project_id": p["project_id"], "full": True, "project": p}


def project_version() -> Dict[str, Any]:
    with _lock:
        p = _project_load()
        return {"version": p["version"], "project_id": p["project_id"]} if p else {"version": 0, "project_id": ""}


def project_replace(project: Dict[str, Any]) -> Dict[str, Any]:
    """打开新文件 / 迁移 / 用本机版本覆盖服务器。"""
    cues = project.get("cues")
    if not isinstance(cues, list) or not cues:
        raise ValueError("字幕为空")
    if len(cues) > MAX_CUES:
        raise ValueError(f"最多 {MAX_CUES} 条字幕")
    pid = str(project.get("project_id") or "").strip()[:64]
    if not pid:
        raise ValueError("缺少 project_id")
    with _lock:
        old = _project_load()
        version = (old["version"] if old else 0) + 1
        clean = [dict(_clean_cue(c), _v=version) for c in cues]
        if len({c["id"] for c in clean}) != len(clean):
            raise ValueError("字幕 id 重复")
        data = {"project_id": pid, "version": version, "updated_at": int(time.time()),
                **{k: str(project.get(k) or "") for k in PROJECT_META}, "count": len(clean), "cues": clean}
        _project_write(data)
        return {"version": version, "project_id": pid}


def project_patch(project_id: str, cues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按条目合并修改。项目已被其他设备换成别的文件时抛出 StoreConflict。"""
    with _lock:
        p = _project_load()
        if not p or p["project_id"] != project_id:
            raise StoreConflict()
        prev = p["version"]
        if not cues:
            return {"version": prev, "prev_version": prev}
        version = prev + 1
        by_id = {c["id"]: c for c in p["cues"]}
        for raw in cues:
            c = _clean_cue(raw)
            cur = by_id.get(c["id"])
            if cur is None:
                continue  # 不支持新增条目；忽略未知 id
            for k in ("start", "end", "text", "translation", "status"):
                cur[k] = c[k]
            cur["_v"] = version
        p["version"] = version
        p["updated_at"] = int(time.time())
        _project_write(p)
        return {"version": version, "prev_version": prev}


def project_delete() -> None:
    with _lock:
        _project_write(None)
