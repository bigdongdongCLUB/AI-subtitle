"""调用 OpenAI 兼容的大模型接口（Groq / 硅基流动 / DeepSeek / OpenAI / Ollama 等）。"""
import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from . import thinking as thinking_mod

log = logging.getLogger("uvicorn.error")  # 与 uvicorn 日志一起输出到 journalctl

MAX_ATTEMPTS = 3            # 限流 / 服务端临时错误时的最多尝试次数
RETRY_STATUS = {429, 500, 502, 503, 504, 529}
MAX_RETRY_WAIT = 30.0       # 单次重试最长等待秒数
PARSE_ATTEMPTS = 2          # 回复整段无法解析时，最多请求几次

LINE_RULE_SINGLE = "每条写成一行，不要换行（显示时会按宽度自动换行）；以 - 开头的多人对话保留 - 标记，用空格隔开。"
LYRIC_RULE = "♪、♫ 等音符之间的歌词保留原文，连同音符原样输出，不要翻译；同一条里的对白照常翻译。"
LINE_RULE_KEEP = "保持原文中的换行结构（换行用 \\n 表示），每行长度适合字幕显示。"
JSON_QUOTE_RULE = 'text 里如果要写英文双引号 "，必须转义成 \\"，否则整段 JSON 会失效。'
RETRY_HINT = ("\n\n注意：你上一次的输出不是合法的 JSON，无法解析。请重新输出完整结果，"
              "只输出一个 JSON 对象；text 里的英文双引号必须写成 \\\"，换行写成 \\n。")

TONE_HINTS = {
    "natural": "自然、口语化，像母语者在日常对话中会说的话。",
    "formal": "正式、书面，用词准确。",
    "concise": "极简字幕风格，尽量精炼，去掉冗余语气词，方便观众快速阅读。",
    "literal": "尽量贴近原文字面含义，不做过多意译。",
    "humorous": "轻松幽默，可适度使用符合目标语言习惯的俏皮表达。",
}


class LLMError(Exception):
    def __init__(self, message: str, status: int = 502, detail: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def env_key_for(base_url: str) -> str:
    """服务器 .env 里的 LLM_API_KEY 只用于 .env 里配置的 LLM_BASE_URL，避免 Key 被发往其他地址。"""
    env_url = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    if env_url and (base_url or "").strip().rstrip("/") == env_url:
        return os.getenv("LLM_API_KEY") or ""
    return ""


def resolve_provider(p: Dict[str, Any]) -> Dict[str, Any]:
    """前端传来的参数为空时，回退到服务器环境变量。"""
    base_url = (p.get("base_url") or os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    api_key = (p.get("api_key") or env_key_for(base_url)).strip()
    model = (p.get("model") or os.getenv("LLM_MODEL") or "").strip()
    if not base_url:
        raise LLMError("未配置 API 地址（base_url）", 400)
    if not model:
        raise LLMError("未配置模型名称（model）", 400)
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "temperature": float(p.get("temperature", 0.3) or 0),
        "timeout": float(p.get("timeout", 120) or 120),
        "json_mode": bool(p.get("json_mode", False)),
        "max_tokens": p.get("max_tokens"),
        "thinking": (p.get("thinking") or "off").strip(),
    }


def thinking_params(base_url: str, model: str, thinking: str) -> Dict[str, Any]:
    """按服务商和模型，把思考档位换成接口参数（规则见 app/thinking.py）。"""
    return thinking_mod.params(base_url, model, thinking)


def _headers(api_key: str) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


async def list_models(provider: Dict[str, Any]) -> List[str]:
    base_url = (provider.get("base_url") or os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    api_key = (provider.get("api_key") or env_key_for(base_url)).strip()
    if not base_url:
        raise LLMError("未配置 API 地址（base_url）", 400)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(f"{base_url}/models", headers=_headers(api_key))
        except httpx.HTTPError as e:
            raise LLMError(f"请求模型列表失败: {e}", 502)
    if r.status_code >= 400:
        raise LLMError(f"模型列表接口返回 {r.status_code}: {r.text[:300]}", r.status_code)
    try:
        data = r.json()
    except ValueError:
        raise LLMError("模型列表接口返回的不是 JSON", 502)
    items = data.get("data") if isinstance(data, dict) else data
    ids: List[str] = []
    for it in items or []:
        if isinstance(it, dict) and it.get("id"):
            ids.append(str(it["id"]))
        elif isinstance(it, str):
            ids.append(it)
    return sorted(set(ids))


def build_messages(items: List[Dict[str, Any]], task: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, str]]:
    mode = task.get("mode", "translate")
    src = (task.get("source_lang") or "自动检测").strip()
    tgt = (task.get("target_lang") or "中文（简体）").strip()
    tone = TONE_HINTS.get(task.get("tone") or "natural", TONE_HINTS["natural"])
    instructions = (task.get("instructions") or "").strip()
    glossary = (task.get("glossary") or "").strip()

    if mode == "polish":
        role = (
            "你是一名资深字幕编辑。请对下面的字幕逐条进行润色，保持每条字幕原本的语言（不要翻译成其他语言）。"
            "修正语病、错别字、标点和生硬的表达，让它读起来自然流畅，同时忠实保留原意。"
        )
    else:
        role = (
            f"你是一名专业影视字幕翻译。请把下面的字幕从「{src}」翻译成「{tgt}」，"
            "译文要地道、自然、符合目标语言的字幕阅读习惯，并忠实于原意。"
        )

    rules = [
        f"语气风格：{tone}",
        "每条字幕独立对应一个 id，必须逐条输出，不能合并、拆分、遗漏或新增条目。",
        LINE_RULE_SINGLE if task.get("single_line", True) else LINE_RULE_KEEP,
        *([LYRIC_RULE] if task.get("skip_lyrics") else []),
        "保留数字、专有名词、人名的常规译法；不要添加解释、注释或引号。",
        "原文中的 <i>、<b> 等样式标签原样保留在对应位置，不要翻译或删除。",
        "如果某条是音效/语气词（例如 [音乐]、嗯），请按目标语言习惯简洁处理。",
        "上下文（context_before / context_after）只用于理解语境，不要翻译它们。",
        '严格只输出 JSON，格式为：{"lines":[{"id":1,"text":"..."}, ...]}，不要输出任何其他内容或 Markdown 代码块。',
        JSON_QUOTE_RULE,
    ]
    if glossary:
        rules.append("术语表（必须遵守，格式为“原文=译文”）：\n" + glossary)
    if instructions:
        rules.append("额外要求：\n" + instructions)

    system = role + "\n\n要求：\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))

    payload = {
        "context_before": [c for c in (context.get("before") or []) if c],
        "lines": [{"id": it["id"], "text": it["text"]} for it in items],
        "context_after": [c for c in (context.get("after") or []) if c],
    }
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_review_messages(items: List[Dict[str, Any]], task: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, str]]:
    """翻译 + 润色的第二步：对照原文校对并润色初译。items 形如 {id, source, draft}。"""
    tgt = (task.get("target_lang") or "中文（简体）").strip()
    tone = TONE_HINTS.get(task.get("tone") or "natural", TONE_HINTS["natural"])
    instructions = (task.get("instructions") or "").strip()
    glossary = (task.get("glossary") or "").strip()

    role = (
        f"你是一名资深影视字幕译审。下面每条字幕给出了原文 source 和「{tgt}」初译 draft。"
        "请对照原文逐条校对并润色初译：修正误译、漏译和不通顺之处，让译文地道自然、简洁易读，符合字幕阅读习惯。"
        "初译已经准确流畅的条目可以保持不变，不要为了改而改。"
    )
    rules = [
        f"语气风格：{tone}",
        f"输出语言必须是「{tgt}」。",
        "每条字幕独立对应一个 id，必须逐条输出，不能合并、拆分、遗漏或新增条目。",
        LINE_RULE_SINGLE if task.get("single_line", True) else LINE_RULE_KEEP,
        *([LYRIC_RULE] if task.get("skip_lyrics") else []),
        "原文中的 <i>、<b> 等样式标签原样保留在对应位置。",
        "不要添加解释、注释或引号；上下文只用于理解语境，不要输出它们。",
        '严格只输出 JSON，格式为：{"lines":[{"id":1,"text":"润色后的译文"}, ...]}，不要输出任何其他内容或 Markdown 代码块。',
        JSON_QUOTE_RULE,
    ]
    if glossary:
        rules.append("术语表（必须遵守，格式为“原文=译文”）：\n" + glossary)
    if instructions:
        rules.append("额外要求：\n" + instructions)
    system = role + "\n\n要求：\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))

    payload = {
        "context_before": [c for c in (context.get("before") or []) if c],
        "lines": [{"id": it["id"], "source": it["source"], "draft": it["draft"]} for it in items],
        "context_after": [c for c in (context.get("after") or []) if c],
    }
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def _strip_wrappers(content: str) -> str:
    s = _THINK_RE.sub("", content or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)   # 去掉 ```json ... ``` 包裹
    if m:
        s = m.group(1).strip()
    return s


_TRAILING_COMMA_RE = re.compile(r",\s*([\]}])")


def extract_json(content: str) -> Any:
    """从模型输出中尽量提取 JSON。允许字符串里出现原始换行，并去掉多余的结尾逗号。"""
    s = _strip_wrappers(content)
    candidates = [s]
    for open_c, close_c in (("{", "}"), ("[", "]")):   # 截取第一个 { 到最后一个 }
        a, b = s.find(open_c), s.rfind(close_c)
        if a != -1 and b > a:
            candidates.append(s[a: b + 1])
    for cand in candidates:
        for text in (cand, _TRAILING_COMMA_RE.sub(r"\1", cand)):
            try:
                return json.loads(text, strict=False)
            except ValueError:
                continue
    raise ValueError("无法解析为 JSON")


# 逐条抢救：整段 JSON 失效时（漏转义的引号、输出被截断等），把还能识别的条目一条条找出来
_ITEM_RES = [
    (re.compile(r'\{\s*"id"\s*:\s*"?(\d+)"?\s*,\s*"text"\s*:\s*"(.*?)"\s*\}(?=\s*(?:,|\]|\}|$))', re.S), 1, 2),
    (re.compile(r'\{\s*"text"\s*:\s*"(.*?)"\s*,\s*"id"\s*:\s*"?(\d+)"?\s*\}(?=\s*(?:,|\]|\}|$))', re.S), 2, 1),
]


def _decode_loose(t: str) -> str:
    """把 JSON 字符串内容解码；里面有没转义的英文双引号时也尽量还原。"""
    for cand in (t, re.sub(r'(?<!\\)"', r'\\"', t)):
        try:
            return json.loads('"' + cand + '"', strict=False)
        except ValueError:
            continue
    return t.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def salvage_items(content: str) -> Dict[int, str]:
    s = _strip_wrappers(content)
    found: Dict[int, str] = {}
    for rx, gi, gt in _ITEM_RES:
        for m in rx.finditer(s):
            iid = int(m.group(gi))
            if iid not in found:
                found[iid] = _decode_loose(m.group(gt))
    return found


def parse_results(content: str, items: List[Dict[str, Any]], info: Optional[Dict[str, Any]] = None) -> Dict[int, str]:
    """把模型输出映射为 {id: 译文}，只返回请求里有的 id。
    info（可选）会写入 method：json / salvage / lines，便于记录日志。"""
    ids = [int(it["id"]) for it in items]
    wanted = set(ids)
    info = info if info is not None else {}
    try:
        data = extract_json(content)
    except ValueError:
        data = None

    if data is not None:
        lines = data.get("lines") if isinstance(data, dict) else data
        if isinstance(data, dict) and lines is None:
            # 形如 {"1": "...", "2": "..."}
            lines = [{"id": k, "text": v} for k, v in data.items()]
        if isinstance(lines, list):
            results: Dict[int, str] = {}
            for ln in lines:
                if isinstance(ln, dict) and "id" in ln:
                    try:
                        iid = int(ln["id"])
                    except (TypeError, ValueError):
                        continue
                    if iid in wanted:
                        results[iid] = str(ln.get("text") or "")
            if results:
                info["method"] = "json"
                return results
            # 纯字符串数组，按顺序对应
            if len(lines) == len(ids) and all(isinstance(x, str) for x in lines):
                info["method"] = "json"
                return dict(zip(ids, lines))

    # JSON 整体失效：逐条抢救（缺的条目由调用方再单独补请求）
    salvaged = {k: v for k, v in salvage_items(content).items() if k in wanted}
    if salvaged:
        info["method"] = "salvage"
        return salvaged

    # 兜底：按行数对应
    plain = _THINK_RE.sub("", content or "").strip()
    rows = [r for r in plain.splitlines() if r.strip()]
    if len(rows) == len(ids) and not plain.lstrip().startswith(("{", "[")):
        info["method"] = "lines"
        cleaned = [re.sub(r"^\s*\d+[\.\):：]\s*", "", r) for r in rows]
        return dict(zip(ids, cleaned))
    raise LLMError("模型返回内容无法解析为逐条结果", 502, {"raw": (content or "")[:500]})


def _retry_wait(r: Optional[httpx.Response], attempt: int) -> float:
    if r is not None:
        ra = r.headers.get("retry-after")
        try:
            if ra is not None:
                return min(MAX_RETRY_WAIT, max(0.0, float(ra)))
        except ValueError:
            pass
        m = re.search(r"try again in ([0-9.]+)\s*(ms|s)", r.text or "")
        if m:
            sec = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
            return min(MAX_RETRY_WAIT, sec + 0.5)
    return min(MAX_RETRY_WAIT, 2.0 * (2 ** attempt) + random.random())


async def _post_once(client: httpx.AsyncClient, url: str, headers: Dict[str, str], body: Dict[str, Any], extras: Dict[str, Any], flags: Optional[Dict[str, Any]] = None) -> httpx.Response:
    """发送一次请求；对方不支持 JSON 模式或思考参数时（400）去掉后再试。"""
    r = await client.post(url, headers=headers, json={**body, **extras})
    if r.status_code == 400 and extras:
        log.warning("LLM model=%s 服务商不接受思考参数 %s（%s），已改为不指定思考深度重试",
                    body.get("model"), json.dumps(extras, ensure_ascii=False), (r.text or "")[:200].replace("\n", " "))
        if flags is not None:
            flags["thinking_ignored"] = True
        r = await client.post(url, headers=headers, json=body)
    if r.status_code == 400 and "response_format" in body:
        body = {k: v for k, v in body.items() if k != "response_format"}
        r = await client.post(url, headers=headers, json=body)
    return r


async def chat_completion(provider: Dict[str, Any], messages: List[Dict[str, str]], tag: str = "") -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": provider["model"],
        "messages": messages,
        "temperature": provider["temperature"],
        "stream": False,
    }
    if provider.get("max_tokens"):
        body["max_tokens"] = int(provider["max_tokens"])
    if provider.get("json_mode"):
        body["response_format"] = {"type": "json_object"}
    extras = thinking_params(provider["base_url"], provider["model"], provider.get("thinking", "off"))

    url = f"{provider['base_url']}/chat/completions"
    headers = _headers(provider["api_key"])
    deadline = provider["timeout"]
    t0 = time.monotonic()
    r: Optional[httpx.Response] = None
    flags: Dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(deadline, connect=min(deadline, 20.0))) as client:
        for attempt in range(MAX_ATTEMPTS):
            ta = time.monotonic()
            try:
                # 总时长上限：部分服务商（如 DeepSeek）排队/推理时持续发送空行保活，
                # 只靠 httpx 的读超时永远不会触发，所以这里限制整个请求的总耗时。
                r = await asyncio.wait_for(_post_once(client, url, headers, body, extras, flags), timeout=deadline)
            except asyncio.TimeoutError:
                log.warning("LLM %s model=%s 超时（%.0fs 内未完成）", tag, provider["model"], deadline)
                raise LLMError(
                    f"模型在 {deadline:.0f} 秒内没有返回结果。可以在模型设置里关闭深度思考、增大超时，或调小每批条数", 504)
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as e:
                log.warning("LLM %s model=%s 连接错误 %s（第 %d 次）", tag, provider["model"], e.__class__.__name__, attempt + 1)
                if attempt + 1 >= MAX_ATTEMPTS:
                    raise LLMError(f"请求模型失败: {e.__class__.__name__} {e}", 502)
                await asyncio.sleep(_retry_wait(None, attempt))
                continue
            except httpx.TimeoutException:
                log.warning("LLM %s model=%s 连接/读取超时", tag, provider["model"])
                raise LLMError("请求模型超时，请增大超时时间或减小批次大小", 504)
            except httpx.HTTPError as e:
                raise LLMError(f"请求模型失败: {e}", 502)

            dur = time.monotonic() - ta
            if r.status_code in RETRY_STATUS and attempt + 1 < MAX_ATTEMPTS and "too large" not in (r.text or "").lower():
                wait = _retry_wait(r, attempt)
                log.warning("LLM %s model=%s -> %d %.1fs，%.1fs 后重试（第 %d 次）", tag, provider["model"], r.status_code, dur, wait, attempt + 1)
                await asyncio.sleep(wait)
                continue
            break

    assert r is not None
    total = time.monotonic() - t0
    if r.status_code >= 400:
        log.warning("LLM %s model=%s -> %d %.1fs %s", tag, provider["model"], r.status_code, total, (r.text or "")[:200].replace("\n", " "))
        try:
            detail = r.json()
        except ValueError:
            detail = r.text[:500]
        raise LLMError(f"模型接口返回 {r.status_code}", r.status_code, detail)

    try:
        data = r.json()
    except ValueError:
        raise LLMError("模型接口返回的不是 JSON", 502, r.text[:500])

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("模型返回结构异常", 502, data)
    usage = data.get("usage") or {}
    finish = choice.get("finish_reason") or ""
    log.info("LLM %s model=%s -> 200 %.1fs tokens=%s finish=%s", tag, provider["model"], total, usage.get("total_tokens", "?"), finish or "?")
    return {"content": content, "usage": usage, "finish_reason": finish, "thinking_ignored": bool(flags.get("thinking_ignored"))}


def _clean(text: str) -> str:
    return (text or "").replace("\\n", "\n").strip()


def _merge_usage(usages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(usages) == 1:
        return usages[0] or {}
    total: Dict[str, Any] = {}
    for u in usages:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance((u or {}).get(k), (int, float)):
                total[k] = total.get(k, 0) + u[k]
    total["calls"] = len(usages)
    return total


def _log_raw(level: int, tag: str, provider: Dict[str, Any], resp: Dict[str, Any], what: str) -> None:
    """把模型的完整原始输出写进日志（一行 JSON 字符串，journalctl 里可以完整看到）。"""
    content = resp.get("content") or ""
    log.log(level, "LLM %s model=%s %s（finish=%s，%d 字符）原始输出：%s", tag, provider["model"], what,
            resp.get("finish_reason") or "?", len(content), json.dumps(content, ensure_ascii=False))


async def _call_and_parse(provider: Dict[str, Any], items: List[Dict[str, Any]], make_messages, tag: str) -> Tuple[Dict[int, str], List[Dict[str, Any]]]:
    """调用模型并解析；整段回复无法解析时自动重新请求（最多 PARSE_ATTEMPTS 次）。"""
    usages: List[Dict[str, Any]] = []
    last_raw = ""
    for attempt in range(PARSE_ATTEMPTS):
        msgs = make_messages(items)
        t = tag
        if attempt:
            msgs = msgs[:-1] + [{**msgs[-1], "content": msgs[-1]["content"] + RETRY_HINT}]
            t = f"{tag} 解析重试{attempt}"
        resp = await chat_completion(provider, msgs, t)
        usages.append(resp["usage"])
        if resp.get("thinking_ignored"):
            usages[-1] = {**(resp["usage"] or {}), "_thinking_ignored": True}
        last_raw = resp["content"]
        info: Dict[str, Any] = {}
        try:
            mapping = parse_results(resp["content"], items, info)
        except LLMError:
            _log_raw(logging.WARNING, t, provider, resp, f"解析失败（第 {attempt + 1}/{PARSE_ATTEMPTS} 次）")
            continue
        if info.get("method") != "json":
            _log_raw(logging.WARNING, t, provider, resp, f"JSON 不合法，已逐条抢救出 {len(mapping)}/{len(items)} 条")
        elif resp.get("finish_reason") == "length":
            _log_raw(logging.WARNING, t, provider, resp, "输出达到长度上限")
        return mapping, usages
    raise LLMError(f"模型连续 {PARSE_ATTEMPTS} 次返回无法解析的内容（完整原始输出已记录在服务器日志）", 502,
                   {"raw": last_raw[:300]})


async def _run_step(provider: Dict[str, Any], items: List[Dict[str, Any]], make_messages, tag: str = "") -> Tuple[Dict[int, str], List[Dict[str, Any]]]:
    """调用一次模型；有漏掉的条目再单独补一次。返回 (结果映射, usage 列表)，结果可能仍缺少部分条目。"""
    tag = f"{tag} #{items[0]['id']}-{items[-1]['id']}({len(items)}条)"
    mapping, usages = await _call_and_parse(provider, items, make_messages, tag)
    missing = [it for it in items if int(it["id"]) not in mapping]
    if missing:
        try:
            m2, u2 = await _call_and_parse(provider, missing, make_messages, tag + f" 补漏{len(missing)}条")
            usages += u2
            mapping.update(m2)
        except LLMError as e:
            log.warning("LLM %s 补漏失败：%s", tag, e.message)
    return mapping, usages


async def translate_items(
    provider_in: Dict[str, Any],
    items: List[Dict[str, Any]],
    task: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """mode: translate（翻译）| polish（润色原文）| translate_polish（先翻译再对照原文润色）"""
    provider = resolve_provider(provider_in)
    ctx = context or {}
    mode = task.get("mode") or "translate"
    first_task = {**task, "mode": "translate" if mode == "translate_polish" else mode}

    step1 = {"translate": "翻译", "polish": "润色"}.get(first_task["mode"], first_task["mode"])
    mapping, usages = await _run_step(provider, items, lambda its: build_messages(its, first_task, ctx), step1)
    still = [int(it["id"]) for it in items if int(it["id"]) not in mapping]
    if len(still) == len(items):
        raise LLMError("模型没有返回任何可用的结果", 502)
    drafts = {int(it["id"]): _clean(mapping[int(it["id"])]) for it in items if int(it["id"]) in mapping}

    warnings: List[str] = []
    if still:
        warnings.append(f"{len(still)} 条未获得结果（#{', #'.join(map(str, still))}），已标为失败，可稍后重试")
    final = dict(drafts)
    if mode == "translate_polish":
        review_items = [{"id": int(it["id"]), "source": it["text"], "draft": drafts[int(it["id"])]} for it in items if int(it["id"]) in drafts]
        try:
            polished, u2 = await _run_step(provider, review_items, lambda its: build_review_messages(its, task, ctx), "译审润色")
            usages += u2
            kept = 0
            for iid in drafts:
                text = _clean(polished.get(iid, ""))
                if text:
                    final[iid] = text
                else:
                    kept += 1
            if kept:
                warnings.append(f"{kept} 条润色未返回结果，已保留初译")
        except LLMError as e:
            # 翻译已经完成，润色失败时保留初译，不让整批作废
            warnings.append(f"润色步骤失败，已保留初译：{e.message}")

    results = [{"id": iid, "text": final[iid]} for iid in sorted(final)]
    out: Dict[str, Any] = {"results": results, "usage": _merge_usage([{k: v for k, v in (u or {}).items() if k != "_thinking_ignored"} for u in usages]),
                           "model": provider["model"], "mode": mode,
                           "thinking": thinking_mod.resolve(thinking_mod.describe(provider["base_url"], provider["model"]), provider.get("thinking")),
                           "thinking_ignored": any((u or {}).get("_thinking_ignored") for u in usages)}
    if warnings:
        out["warning"] = "；".join(warnings)
    return out
