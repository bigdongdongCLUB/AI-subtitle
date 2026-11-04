"""无需 pytest 的自检脚本：python tests/test_api.py
覆盖字幕解析/导出、模型结果解析，以及用模拟 LLM 跑通 /api/* 接口。"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

import app.llm as llm  # noqa: E402
from app.main import app  # noqa: E402
from app.subtitles import build_subtitle, parse_subtitle  # noqa: E402

SRT = b"1\n00:00:01,000 --> 00:00:03,500\nHello, how are you?\n\n2\n00:00:04,000 --> 00:00:06,000\nI'm fine, thanks.\nAnd you?\n\n3\n00:00:07,250 --> 00:00:09,000\n<i>Never better.</i>\n"
VTT = b"WEBVTT\n\nintro\n00:01.000 --> 00:03.500 align:start\nHello\n\n00:00:04.000 --> 00:00:06.000\nBye\n"
ASS = b"""[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.50,Default,Tom,0,0,0,,{\\pos(100,200)}Hello\\NWorld
Comment: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,note
"""


TEST_USER, TEST_PASS = "admin", "test-pass-123"


async def login(c):
    """首次运行时完成设置，之后登录；把会话 Cookie 带在之后的请求里。"""
    st = (await c.get("/api/auth/status")).json()
    if not st["configured"]:
        r = await c.post("/api/auth/setup", json={"username": TEST_USER, "password": TEST_PASS})
    else:
        r = await c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASS})
    assert r.status_code == 200, r.text
    token = r.cookies.get("ais_session") or r.headers["set-cookie"].split("ais_session=")[1].split(";")[0]
    c.cookies.clear()
    c.headers["Cookie"] = f"ais_session={token}"
    return token


def test_subtitles():
    r = parse_subtitle(SRT, "a.srt")
    assert r["format"] == "srt" and r["count"] == 3
    assert r["cues"][1]["text"] == "I'm fine, thanks.\nAnd you?" and r["cues"][2]["start"] == 7250
    r = parse_subtitle(VTT, "a.vtt")
    assert r["format"] == "vtt" and r["count"] == 2 and r["cues"][0]["meta"]["vtt_id"] == "intro"
    out, _ = build_subtitle(r["cues"], "vtt", "original")
    assert "intro\n00:00:01.000 --> 00:00:03.500 align:start" in out
    r = parse_subtitle(ASS, "a.ass")
    assert r["format"] == "ass" and r["cues"][0]["text"] == "Hello\nWorld" and r["cues"][0]["meta"]["Name"] == "Tom"
    r["cues"][0]["translation"] = "你好\n世界"
    out, _ = build_subtitle(r["cues"], "ass", "translation", r["header"])
    assert "Dialogue: 0,0:00:01.00,0:00:03.50,Default,Tom,0,0,0,,你好\\N世界" in out
    assert "Comment: 0,0:00:07.00" in out
    out, _ = build_subtitle(r["cues"], "srt", "translation", r["header"])
    assert "note" not in out, "Comment 事件不应进入 SRT"
    gbk = "1\n00:00:01,000 --> 00:00:02,000\n你好，世界\n".encode("gb18030")
    assert parse_subtitle(gbk, "g.srt")["cues"][0]["text"] == "你好，世界"
    print("subtitles ok")


def test_parse_results():
    items = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]
    assert llm.parse_results('```json\n{"lines":[{"id":1,"text":"甲"},{"id":2,"text":"乙"}]}\n```', items) == {1: "甲", 2: "乙"}
    assert llm.parse_results('<think>x</think>[{"id":2,"text":"乙"},{"id":1,"text":"甲"}]', items) == {2: "乙", 1: "甲"}
    assert llm.parse_results('{"1":"甲","2":"乙"}', items) == {1: "甲", 2: "乙"}
    assert llm.parse_results("1. 甲\n2. 乙", items) == {1: "甲", 2: "乙"}
    # 只润色：保持原文语言，提示词中不出现目标语言
    m = llm.build_messages(items, {"mode": "polish", "target_lang": "日本語"}, {})
    assert "保持每条字幕原本的语言" in m[0]["content"] and "日本語" not in m[0]["content"]
    m = llm.build_review_messages([{"id": 1, "source": "a", "draft": "甲"}], {"target_lang": "日本語", "glossary": "a=甲"}, {})
    assert "日本語" in m[0]["content"] and "a=甲" in m[0]["content"] and '"draft": "甲"' in m[1]["content"]
    print("parse_results ok")


async def test_api():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m1"}]})
        if req.headers.get("Authorization") != "Bearer k":
            return httpx.Response(401, json={"error": "bad key"})
        msgs = json.loads(req.content)["messages"]
        user = json.loads(msgs[1]["content"])
        if "译审" in msgs[0]["content"]:  # 翻译+润色的第二步
            if req.headers.get("X-Fail-Review") or "REVIEW_FAIL" in msgs[0]["content"]:
                return httpx.Response(429, json={"error": "rate limited"}, headers={"Retry-After": "0"})
            lines = [{"id": l["id"], "text": "润" + l["draft"]} for l in user["lines"]]
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"lines": lines})}}], "usage": {"total_tokens": 5}})
        lines = [{"id": l["id"], "text": "译" + l["text"]} for l in user["lines"]]
        if len(lines) > 1 and len(calls) == 2:
            lines = lines[:-1]  # 漏一条，测试补齐
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"lines": lines})}}]})

    orig = httpx.AsyncClient

    class Mock(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    llm.httpx.AsyncClient = Mock
    try:
        async with orig(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/health")).json()["ok"]
            await login(c)
            assert "AI 字幕工作台" in (await c.get("/")).text
            proj = (await c.post("/api/parse", files={"file": ("t.srt", SRT)})).json()
            assert proj["count"] == 3
            assert (await c.post("/api/parse", files={"file": ("x.srt", b"nope")})).status_code == 400
            assert (await c.post("/api/models", json={"base_url": "http://m/v1"})).json()["models"] == ["m1"]
            prov = {"base_url": "http://m/v1", "api_key": "k", "model": "m1"}
            items = [{"id": x["id"], "text": x["text"]} for x in proj["cues"]]
            r = await c.post("/api/translate", json={"provider": prov, "items": items})
            assert r.status_code == 200 and len(r.json()["results"]) == 3 and len(calls) == 3, r.text
            assert (await c.post("/api/translate", json={"provider": {**prov, "api_key": "bad"}, "items": items})).status_code == 401
            assert (await c.post("/api/translate", json={"provider": {"model": "m"}, "items": items})).status_code == 400
            # 翻译 + 润色：两步，结果来自第二步
            n0 = len(calls)
            r2 = await c.post("/api/translate", json={"provider": prov, "task": {"mode": "translate_polish"}, "items": items})
            j2 = r2.json()
            assert r2.status_code == 200 and "warning" not in j2, r2.text
            assert [x["text"] for x in j2["results"]][0] == "润译Hello, how are you?", j2
            assert len(calls) - n0 == 2 and j2["usage"].get("calls") == 2, (len(calls) - n0, j2["usage"])
            # 润色这一步失败 → 保留初译并给出提示
            r3 = await c.post("/api/translate", json={"provider": prov, "task": {"mode": "translate_polish", "instructions": "REVIEW_FAIL"}, "items": items})
            j3 = r3.json()
            assert r3.status_code == 200 and "润色步骤失败" in j3.get("warning", ""), r3.text
            assert j3["results"][0]["text"] == "译Hello, how are you?", j3
            # 非法模式
            assert (await c.post("/api/translate", json={"provider": prov, "task": {"mode": "xx"}, "items": items})).status_code == 400
            for x, t in zip(proj["cues"], r.json()["results"]):
                x["translation"] = t["text"]
            r = await c.post("/api/export", json={"cues": proj["cues"], "format": "srt", "content": "bilingual_ot", "filename": "字幕"})
            assert r.status_code == 200 and "译Hello" in r.text and "%E5%AD%97%E5%B9%95.srt" in r.headers["content-disposition"]
            assert (await c.post("/api/export", json={"cues": [], "format": "xyz"})).status_code == 400
    finally:
        llm.httpx.AsyncClient = orig
    print("api ok")


async def test_robustness():
    """总超时、限流重试、关闭深度思考参数、浏览器断开后取消。"""
    import time
    from fastapi.responses import JSONResponse
    import app.main as main

    orig = httpx.AsyncClient
    state = {"calls": 0, "bodies": []}

    def ok_response(body):
        user = json.loads(body["messages"][1]["content"])
        lines = [{"id": l["id"], "text": "译" + l["text"]} for l in user["lines"]]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"lines": lines})}}]})

    async def handler(req: httpx.Request):
        state["calls"] += 1
        body = json.loads(req.content)
        state["bodies"].append(body)
        model = body["model"]
        if model == "dribble":  # 模拟 DeepSeek：一直发空行保活，永不结束
            async def gen():
                while True:
                    yield b"\n"
                    await asyncio.sleep(0.1)
            return httpx.Response(200, content=gen())
        if model == "flaky" and state["calls"] == 1:
            return httpx.Response(429, json={"error": "slow down"}, headers={"Retry-After": "0"})
        if model == "picky" and "thinking" in body:
            return httpx.Response(400, json={"error": "unknown field thinking"})
        if model == "slow":
            await asyncio.sleep(5)
        return ok_response(body)

    class Mock(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    llm.httpx.AsyncClient = Mock
    items = [{"id": 1, "text": "Hi"}]
    try:
        # 1. 保活空行不会让请求无限挂起：总超时 1 秒
        t = time.monotonic()
        try:
            await llm.translate_items({"base_url": "http://m/v1", "model": "dribble", "timeout": 1}, items, {})
            raise AssertionError("应当超时")
        except llm.LLMError as e:
            assert e.status == 504 and time.monotonic() - t < 3, (e.status, time.monotonic() - t)
        # 2. 429 自动重试后成功
        state.update(calls=0, bodies=[])
        r = await llm.translate_items({"base_url": "http://m/v1", "model": "flaky"}, items, {})
        assert r["results"][0]["text"] == "译Hi" and state["calls"] == 2
        # 3. DeepSeek 默认带关闭思考参数；选择“模型默认”时不带
        state.update(calls=0, bodies=[])
        await llm.translate_items({"base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-pro"}, items, {})
        assert state["bodies"][0].get("thinking") == {"type": "disabled"}
        state.update(calls=0, bodies=[])
        await llm.translate_items({"base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-pro", "thinking": "default"}, items, {})
        assert "thinking" not in state["bodies"][0]
        # 对方不认识思考参数（400）→ 去掉后重试成功
        state.update(calls=0, bodies=[])
        r = await llm.translate_items({"base_url": "https://api.deepseek.com/v1", "model": "picky"}, items, {})
        assert r["results"][0]["text"] == "译Hi" and state["calls"] == 2 and "thinking" not in state["bodies"][1]
        # 4. 浏览器断开 → 取消模型调用，约 1 秒内返回 499
        class FakeRequest:
            async def is_disconnected(self):
                return True
        req = main.TranslateRequest(provider={"base_url": "http://m/v1", "model": "slow"}, items=items)
        t = time.monotonic()
        resp = await main.api_translate(req, FakeRequest())
        assert isinstance(resp, JSONResponse) and resp.status_code == 499 and time.monotonic() - t < 2.5
    finally:
        llm.httpx.AsyncClient = orig
    print("robustness ok")


async def test_settings_store():
    """设置保存在服务器：Key 不下发、地址变更清除 Key、版本冲突、按配置 id 使用 Key、防止 Key 外泄。"""
    import stat
    from app import store

    orig = httpx.AsyncClient
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.url.host, req.headers.get("Authorization")))
        if req.url.path.endswith("/models"):
            if req.headers.get("Authorization") != "Bearer k":
                return httpx.Response(401, json={"error": "bad key"})
            return httpx.Response(200, json={"data": [{"id": "m1"}]})
        if req.headers.get("Authorization") != "Bearer k":
            return httpx.Response(401, json={"error": "bad key"})
        user = json.loads(json.loads(req.content)["messages"][1]["content"])
        lines = [{"id": l["id"], "text": "译" + l["text"]} for l in user["lines"]]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"lines": lines})}}]})

    class Mock(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    llm.httpx.AsyncClient = Mock
    try:
        async with orig(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await login(c)
            g = (await c.get("/api/settings")).json()
            assert g["profiles"]["list"] == [] and g["profiles"]["version"] == 0
            prof = {"id": "a", "name": "", "preset": "custom", "base_url": "http://m/v1", "model": "m1"}
            r = await c.put("/api/settings/profiles", json={"base_version": 0, "activeId": "a", "list": [{**prof, "api_key": "k"}]})
            assert r.status_code == 200, r.text
            pub = r.json()["profiles"]
            assert "api_key" not in json.dumps(pub) and pub["list"][0]["has_key"] and pub["version"] == 1
            assert "k" == store.get_profile("a")["api_key"]
            f = store.data_file()
            assert stat.S_IMODE(os.stat(f).st_mode) == 0o600 and "api_key" in f.read_text()
            # GET 不含明文 Key
            assert '"k"' not in (await c.get("/api/settings")).text
            # 不带 api_key 保存 → 保留 Key；复制到同地址 → 复制 Key；复制到不同地址 → 不复制
            r = await c.put("/api/settings/profiles", json={"base_version": 1, "activeId": "a", "list": [
                {**prof, "name": "改名"}, {**prof, "id": "b", "copy_key_from": "a"}, {**prof, "id": "c", "base_url": "http://x/v1", "copy_key_from": "a"}]})
            assert r.status_code == 200, r.text
            assert [store.get_profile(i)["api_key"] for i in "abc"] == ["k", "k", ""]
            # 旧版本号 → 409，并返回最新内容
            r = await c.put("/api/settings/profiles", json={"base_version": 1, "activeId": "a", "list": [prof]})
            assert r.status_code == 409 and r.json()["current"]["profiles"]["version"] == 2
            # 切换使用中的配置
            r = await c.put("/api/settings/active", json={"activeId": "b"})
            assert r.json()["profiles"]["activeId"] == "b"
            assert (await c.put("/api/settings/active", json={"activeId": "zz"})).status_code == 404
            # 任务设置
            t = (await c.put("/api/settings/task", json={"task": {"mode": "translate_polish", "glossary": "a=甲", "evil": 1}})).json()
            assert t["value"] == {"mode": "translate_polish", "glossary": "a=甲"} and t["version"] == 1
            t = (await c.put("/api/settings/task", json={"task": {"line_merge": 0, "line_width": 999, "font_size": "x", "skip_lyrics": 1}})).json()
            assert t["value"]["skip_lyrics"] is True and t["value"]["line_merge"] is False and t["value"]["line_width"] == 120 and t["value"]["font_size"] == 44, t
            r = await c.post("/api/export", json={"cues": [{"start": 0, "end": 900, "text": "a", "translation": "甲"}], "format": "ass", "content": "bilingual_to", "font_size": 80, "secondary_scale": 70, "play_res": 2160})
            assert "Style: Default,Arial,80," in r.text and "Style: Secondary,Arial,56," in r.text and "PlayResY: 2160" in r.text, r.text
            t = (await c.put("/api/settings/task", json={"task": {"export_res": 2160}})).json()
            assert t["value"]["export_res"] == 2160
            t = (await c.put("/api/settings/task", json={"task": {"export_res": 480}})).json()
            assert t["value"]["export_res"] == 1080
            # 导出格式和内容：记住上次的选择，不合法的值不覆盖
            t = (await c.put("/api/settings/task", json={"task": {"export_format": "ass", "export_content": "original"}})).json()
            assert t["value"]["export_format"] == "ass" and t["value"]["export_content"] == "original", t
            t = (await c.put("/api/settings/task", json={"task": {"export_format": "", "export_content": "bad"}})).json()
            assert t["value"]["export_format"] == "ass" and t["value"]["export_content"] == "original", t
            r = await c.post("/api/export", json={"cues": [{"start": 0, "end": 900, "text": "a", "translation": "甲"}], "format": "ass", "content": "bilingual_to", "font_size": 40, "secondary_scale": 70})
            assert "Style: Default,Arial,40," in r.text and "Style: Secondary,Arial,28," in r.text and "甲\\N{\\rSecondary}a{\\r}" in r.text, r.text
            # 字体和颜色：经过接口校验后写入两个样式
            r = await c.post("/api/export", json={"cues": [{"start": 0, "end": 900, "text": "a", "translation": "甲"}], "format": "ass", "content": "bilingual_to",
                                                  "style": {"font": "Microsoft YaHei", "color": "#ffe066", "outline_color": "#101010", "font2": "Bad,{Name}", "color2": "nope"}})
            assert "Style: Default,Microsoft YaHei,44,&H0066E0FF,&H000000FF,&H00101010," in r.text, r.text
            assert "Style: Secondary,BadName,33,&H00FFFFFF," in r.text, r.text
            t = (await c.put("/api/settings/task", json={"task": {"ass_style": {"font": "KaiTi", "color": "#123456", "color2": "x"}}})).json()
            assert t["value"]["ass_style"]["font"] == "KaiTi" and t["value"]["ass_style"]["color"] == "#123456" and t["value"]["ass_style"]["color2"] == "#FFFFFF", t
            assert (await c.get("/api/settings")).json()["task"]["value"]["glossary"] == "a=甲"
            # 浏览器只发 profile_id，服务器用保存的 Key
            items = [{"id": 1, "text": "Hi"}]
            seen.clear()
            r = await c.post("/api/translate", json={"provider": {"profile_id": "a", "base_url": "http://m/v1", "model": "m1"}, "items": items})
            assert r.status_code == 200 and r.json()["results"][0]["text"] == "译Hi", r.text
            assert (await c.post("/api/models", json={"profile_id": "a", "base_url": "http://m/v1"})).json()["models"] == ["m1"]
            # 地址不一致 → 拒绝，且不发出任何请求
            seen.clear()
            r = await c.post("/api/translate", json={"provider": {"profile_id": "a", "base_url": "http://evil/v1", "model": "m1"}, "items": items})
            assert r.status_code == 400 and not seen, (r.status_code, seen)
            # 修改地址后不带 Key 保存 → 服务器清除 Key
            v = (await c.get("/api/settings")).json()["profiles"]["version"]
            await c.put("/api/settings/profiles", json={"base_version": v, "activeId": "a", "list": [{**prof, "base_url": "http://evil/v1"}]})
            assert store.get_profile("a")["api_key"] == ""
            # .env 的 Key 只发给 .env 的地址
            os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"] = "http://m/v1", "k"
            try:
                seen.clear()
                r = await c.post("/api/translate", json={"provider": {"base_url": "http://evil/v1", "model": "m1"}, "items": items})
                assert r.status_code == 401 and seen and seen[0] == ("evil", None), (r.status_code, seen)
                r = await c.post("/api/translate", json={"provider": {"base_url": "", "model": "m1"}, "items": items})
                assert r.status_code == 200, r.text
            finally:
                del os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"]
    finally:
        llm.httpx.AsyncClient = orig
    print("settings store ok")


def test_thinking():
    """思考深度：各服务商的档位与参数、不支持的档位就近换算。"""
    from app import thinking as T
    ids = lambda b, m: [x["id"] for x in T.options(b, m)["levels"]]
    DS, GQ, OA = "https://api.deepseek.com/v1", "https://api.groq.com/openai/v1", "https://api.openai.com/v1"
    SF, DSC, ZP = "https://api.siliconflow.cn/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1", "https://open.bigmodel.cn/api/paas/v4"
    # DeepSeek：关闭 + low/high/max（实测有效）
    assert ids(DS, "deepseek-v4-pro") == ["off", "low", "high", "max", "default"]
    assert T.params(DS, "deepseek-v4-pro", "off") == {"thinking": {"type": "disabled"}}
    assert T.params(DS, "deepseek-v4-pro", "low") == {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    assert T.params(DS, "deepseek-v4-pro", "max") == {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}
    assert T.params(DS, "deepseek-v4-pro", "medium") == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}   # 没有 medium，就近
    assert T.params(DS, "deepseek-v4-pro", "default") == {}
    assert ids(DS, "deepseek-chat") == ["default"] and T.params(DS, "deepseek-chat", "high") == {}
    # Groq gpt-oss：不能关闭，关闭 → 轻度
    assert ids(GQ, "openai/gpt-oss-120b") == ["low", "medium", "high", "default"]
    assert T.params(GQ, "openai/gpt-oss-120b", "off") == {"reasoning_effort": "low"}
    assert T.params(GQ, "openai/gpt-oss-120b", "max") == {"reasoning_effort": "high"}
    assert T.params(GQ, "qwen/qwen3.8-27b", "off") == {"reasoning_effort": "none"}
    assert ids(GQ, "llama-3.3-70b-versatile") == ["default"]
    # OpenAI
    assert T.params(OA, "gpt-5-mini", "off") == {"reasoning_effort": "minimal"}
    assert T.params(OA, "gpt-5.2", "off") == {"reasoning_effort": "none"}
    assert ids(OA, "o4-mini") == ["low", "medium", "high", "default"] and ids(OA, "gpt-4o-mini") == ["default"]
    # OpenRouter
    assert T.params("https://openrouter.ai/api/v1", "x/y", "medium") == {"reasoning": {"effort": "medium"}}
    # 硅基流动 / 百炼：开关 + 思考预算
    assert T.params(SF, "Qwen/Qwen3-32B", "off") == {"enable_thinking": False}
    assert T.params(SF, "Qwen/Qwen3-32B", "high") == {"enable_thinking": True, "thinking_budget": 16384}
    assert T.params(SF, "deepseek-ai/DeepSeek-R1", "off") == {"thinking_budget": 1024}
    assert ids(SF, "Qwen/Qwen2.5-72B-Instruct") == ["default"] and ids(SF, "Qwen/Qwen3-235B-A22B-Instruct-2507") == ["default"]
    assert T.params(DSC, "qwen-plus", "low") == {"enable_thinking": True, "thinking_budget": 1024}
    # 智谱：只能开关
    assert ids(ZP, "glm-4.6") == ["off", "on", "default"] and T.params(ZP, "glm-4.6", "high") == {"thinking": {"type": "enabled"}}
    assert ids(ZP, "glm-4-flash") == ["default"]
    # 其他服务：通用 reasoning_effort
    assert T.params("http://my-vllm:8000/v1", "m", "off") == {"reasoning_effort": "none"}
    # 未知档位 → 关闭；旧版保存的 off/default 照常
    assert T.params(DS, "deepseek-v4-pro", "weird") == {"thinking": {"type": "disabled"}}
    o = T.options(GQ, "openai/gpt-oss-120b", "off")
    assert o["resolved"] == "low" and o["adjustable"] and "default" not in o["slow"]
    o = T.options(DS, "deepseek-v4-pro", "default")
    assert o["resolved"] == "default" and set(o["slow"]) == {"high", "max", "default"} and "off" not in o["temp_ignored"]
    print("thinking ok")


async def test_thinking_api():
    """接口：档位查询、配置保存新档位、服务商拒绝思考参数时回退并标记。"""
    from app import store
    orig = httpx.AsyncClient
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append(body)
        if body["model"] == "strict" and ("reasoning_effort" in body or "thinking" in body):
            return httpx.Response(400, json={"error": "unknown parameter"})
        user = json.loads(body["messages"][1]["content"])
        lines = [{"id": l["id"], "text": "译" + l["text"]} for l in user["lines"]]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"lines": lines})}, "finish_reason": "stop"}]})

    class Mock(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    llm.httpx.AsyncClient = Mock
    try:
        async with orig(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await login(c)
            r = (await c.post("/api/thinking/options", json={"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "current": "off"})).json()
            assert r["resolved"] == "low" and [x["id"] for x in r["levels"]] == ["low", "medium", "high", "default"], r
            assert r["levels"][1]["label"] == "中度（medium，模型默认）", r["levels"]
            # 配置可以保存新档位，非法值存为 off
            v = (await c.get("/api/settings")).json()["profiles"]["version"]
            r = await c.put("/api/settings/profiles", json={"base_version": v, "activeId": "t1", "list": [
                {"id": "t1", "preset": "deepseek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-pro", "thinking": "max", "api_key": "k"},
                {"id": "t2", "preset": "custom", "base_url": "http://m/v1", "model": "m", "thinking": "bogus"}]})
            lst = r.json()["profiles"]["list"]
            assert [x["thinking"] for x in lst] == ["max", "off"], lst
            # 翻译时按档位发送参数，并返回实际档位
            seen.clear()
            r = (await c.post("/api/translate", json={"provider": {"base_url": "https://api.deepseek.com/v1", "api_key": "k", "model": "deepseek-v4-pro", "thinking": "low"},
                                                     "items": [{"id": 1, "text": "Hi"}]})).json()
            assert seen[0]["reasoning_effort"] == "low" and seen[0]["thinking"] == {"type": "enabled"} and r["thinking"] == "low" and r["thinking_ignored"] is False, (seen[0], r)
            # 服务商拒绝思考参数 → 去掉参数重试成功，并标记 thinking_ignored
            seen.clear()
            r = (await c.post("/api/translate", json={"provider": {"base_url": "http://m/v1", "model": "strict", "thinking": "high"},
                                                     "items": [{"id": 1, "text": "Hi"}]})).json()
            assert r["results"][0]["text"] == "译Hi" and r["thinking_ignored"] is True and len(seen) == 2 and "reasoning_effort" not in seen[1], (r, seen)
    finally:
        llm.httpx.AsyncClient = orig
    print("thinking api ok")


def test_layout_export():
    """ASS：默认字号 44，可调；双语时原文缩小；导入的 ASS 沿用原样式；提示词要求单行。SRT 不变。"""
    cues = [{"start": 0, "end": 1000, "text": "Hello there\nfriend", "translation": "你好 朋友"}]
    out, _ = build_subtitle(cues, "ass", "bilingual_to")
    assert "Style: Default,Arial,44," in out
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,你好 朋友\\N{\\rSecondary}Hello there\\Nfriend{\\r}" in out, out
    assert "Style: Secondary,Arial,33," in out, out
    out, _ = build_subtitle(cues, "ass", "bilingual_ot", "", 36, 60)
    assert "Style: Default,Arial,36," in out and "Style: Secondary,Arial,22," in out and ",,{\\rSecondary}Hello there\\Nfriend{\\r}\\N你好 朋友" in out, out
    out, _ = build_subtitle(cues, "ass", "translation")
    assert "\\rSecondary" not in out and out.rstrip().endswith(",,你好 朋友")
    r = parse_subtitle(ASS, "a.ass")
    out, _ = build_subtitle(r["cues"], "ass", "original", r["header"], 48)
    assert "Style: Default,Arial,48," in out, "导入的 ASS 字号不变时应沿用自己的样式"
    out, _ = build_subtitle(cues, "srt", "bilingual_to")
    assert out == "1\n00:00:00,000 --> 00:00:01,000\n你好 朋友\nHello there\nfriend\n", repr(out)
    # 分辨率：画布、描边、阴影、边距随画面高度换算
    out, _ = build_subtitle(cues, "ass", "translation", "", 29, 75, 720)
    assert "PlayResX: 1280\nPlayResY: 720" in out and "Style: Default,Arial,29," in out and ",1,1.7,0.7,2,20,20,20,1" in out, out
    out, _ = build_subtitle(cues, "ass", "translation", "", 88, 75, 2160)
    assert "PlayResX: 3840\nPlayResY: 2160" in out and "Style: Default,Arial,88," in out and ",1,5,2,2,60,60,60,1" in out, out
    out, _ = build_subtitle(cues, "ass", "translation", "", 44, 75, 999)          # 非法分辨率 → 1080
    assert "PlayResY: 1080" in out
    # 导入的 ASS：沿用画布，按主要样式的字号比例缩放所有样式
    from app.subtitles import ass_main_style_size
    r = parse_subtitle(ASS, "a.ass")
    assert ass_main_style_size(r["header"], r["cues"]) == 48
    out, _ = build_subtitle(r["cues"], "ass", "original", r["header"], 36, 75, 2160)
    assert "Style: Default,Arial,36," in out and "PlayResY: 2160" not in out, out
    out, _ = build_subtitle(r["cues"], "ass", "original", r["header"], 48, 75, 720)
    assert "Style: Default,Arial,48," in out and "Style: Secondary,Arial,36," in out
    from app.subtitles import ass_main_style
    assert ass_main_style(r["header"], r["cues"]) == {"name": "Default", "font": "Arial", "size": 48.0, "color": "#FFFFFF", "outline_color": "#000000"}
    out, _ = build_subtitle(r["cues"], "ass", "original", r["header"], 48, 50, 1080,
                            {"font": "KaiTi", "color": "#FF0000", "outline_color": "#00FF00", "font2": "Arial", "color2": "#0000FF", "outline_color2": "#000000"})
    assert "Style: Default,KaiTi,48,&H000000FF,&H000000FF,&H0000FF00," in out, out
    assert "Style: Secondary,Arial,24,&H00FF0000," in out, out
    m = llm.build_messages([{"id": 1, "text": "a"}], {}, {})[0]["content"]
    assert llm.LINE_RULE_SINGLE in m and llm.LINE_RULE_KEEP not in m
    m = llm.build_messages([{"id": 1, "text": "a"}], {"single_line": False}, {})[0]["content"]
    assert llm.LINE_RULE_KEEP in m
    m = llm.build_review_messages([{"id": 1, "source": "a", "draft": "甲"}], {}, {})[0]["content"]
    assert llm.LINE_RULE_SINGLE in m
    # 忽略歌词：开启时提示词要求保留歌词原文，翻译和润色两步都有
    assert llm.LYRIC_RULE not in llm.build_messages([{"id": 1, "text": "a"}], {}, {})[0]["content"]
    assert llm.LYRIC_RULE in llm.build_messages([{"id": 1, "text": "a"}], {"skip_lyrics": True}, {})[0]["content"]
    assert llm.LYRIC_RULE in llm.build_review_messages([{"id": 1, "source": "a", "draft": "甲"}], {"skip_lyrics": True}, {})[0]["content"]
    print("layout export ok")


def test_reflow_js():
    """字幕排版逻辑在前端（static/reflow.js），有 node 时一起跑。"""
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        print("reflow js skipped (no node)")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([node, os.path.join(here, "test_reflow.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    print(r.stdout.strip())


def test_parse_tolerance():
    """更宽容的解析：漏转义的引号、字符串里的原始换行、多余逗号、截断输出、无关 id。"""
    items = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}, {"id": 3, "text": "c"}]
    P = llm.parse_results
    # 漏转义的英文双引号（这次 DeepSeek 出错的典型情况）
    bad_quote = '{"lines":[{"id":1,"text":""desperate for my help."},{"id":2,"text":"“我迎上前去"},{"id":3,"text":"想让他知道\\n我有多享受这一切。""}]}'
    info = {}
    r = P(bad_quote, items, info)
    assert r == {1: '"desperate for my help.', 2: "“我迎上前去", 3: '想让他知道\n我有多享受这一切。"'}, r
    assert info["method"] == "salvage"
    # 字符串里直接换行（不合规但常见）
    info = {}
    assert P('{"lines":[{"id":1,"text":"第一行\n第二行"}]}'.replace("\\n", "\n"), items[:1], info) == {1: "第一行\n第二行"} and info["method"] == "json"
    # 多余的结尾逗号
    assert P('{"lines":[{"id":1,"text":"甲"},{"id":2,"text":"乙"},]}', items[:2]) == {1: "甲", 2: "乙"}
    # 被截断：能抢救的先返回，缺的由上层补请求
    info = {}
    assert P('{"lines":[{"id":1,"text":"甲"},{"id":2,"text":"乙"},{"id":3,"text":"丙', items, info) == {1: "甲", 2: "乙"} and info["method"] == "salvage"
    # text 在 id 前面
    assert P('{"lines":[{"text":"甲","id":1},{"text":""乙","id":2}]}', items[:2]) == {1: "甲", 2: '"乙'}
    # 不是请求里的 id 会被忽略
    assert P('{"lines":[{"id":9,"text":"x"},{"id":1,"text":"甲"}]}', items[:1]) == {1: "甲"}
    # 完全无法识别仍然报错
    try:
        P("{broken", items)
        raise AssertionError("应当报错")
    except llm.LLMError:
        pass
    print("parse tolerance ok")


async def test_parse_retry():
    """整段无法解析时自动重试；截断时补请求缺失条目；仍缺失时返回部分结果；完整原始输出写入日志。"""
    import logging as _logging
    orig = httpx.AsyncClient
    state = {"calls": []}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        user = json.loads(body["messages"][1]["content"].split("\n\n注意：")[0])
        retry = "上一次的输出不是合法的 JSON" in body["messages"][1]["content"]
        state["calls"].append((body["model"], [l["id"] for l in user["lines"]], retry))
        model = body["model"]
        ok = lambda ids: json.dumps({"lines": [{"id": i, "text": f"译{i}"} for i in ids]})
        ids = [l["id"] for l in user["lines"]]
        if model == "badonce":
            content = "抱歉，我没法按格式输出。" if not retry else ok(ids)
        elif model == "alwaysbad":
            content = '{"lines": [ BROKEN-OUTPUT-MARKER'
        elif model == "truncated":
            content = ok(ids)[:-20] if len(ids) > 1 else ok(ids)
        elif model == "partial":
            content = ok([i for i in ids if i == 1])   # 第 2 条永远不返回
        else:
            content = ok(ids)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    class Mock(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    records = []
    class H(_logging.Handler):
        def emit(self, rec):
            records.append(rec.getMessage())
    h = H(); llm.log.addHandler(h)
    llm.httpx.AsyncClient = Mock
    items = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}, {"id": 3, "text": "c"}]
    prov = lambda m: {"base_url": "http://m/v1", "model": m}
    try:
        # 1. 第一次乱码，第二次正常：自动重试，第二次请求带上修正提示
        state["calls"].clear()
        r = await llm.translate_items(prov("badonce"), items, {})
        assert [x["text"] for x in r["results"]] == ["译1", "译2", "译3"] and "warning" not in r, r
        assert [c[2] for c in state["calls"]] == [False, True], state["calls"]
        # 2. 连续两次都无法解析：报错，且完整原始输出写进了日志
        state["calls"].clear(); records.clear()
        try:
            await llm.translate_items(prov("alwaysbad"), items, {})
            raise AssertionError("应当报错")
        except llm.LLMError as e:
            assert "连续 2 次" in e.message and len(state["calls"]) == 2, (e.message, state["calls"])
        raw_logs = [m for m in records if "原始输出" in m]
        assert len(raw_logs) == 2 and all("BROKEN-OUTPUT-MARKER" in m for m in raw_logs), records
        # 3. 输出被截断：先抢救已有条目，再只为缺失的条目补请求
        state["calls"].clear(); records.clear()
        r = await llm.translate_items(prov("truncated"), items, {})
        assert [x["id"] for x in r["results"]] == [1, 2, 3] and "warning" not in r, r
        assert [c[1] for c in state["calls"]] == [[1, 2, 3], [3]], state["calls"]
        assert any("逐条抢救" in m for m in records), records
        # 4. 补请求后仍缺：返回已有的结果，并提示哪些条目失败
        state["calls"].clear()
        r = await llm.translate_items(prov("partial"), items[:2], {})
        assert [x["id"] for x in r["results"]] == [1] and "1 条未获得结果" in r["warning"] and "#2" in r["warning"], r
        # 5. 翻译+润色：部分缺失时只润色拿到的条目
        state["calls"].clear()
        r = await llm.translate_items(prov("partial"), items[:2], {"mode": "translate_polish"})
        assert [x["id"] for x in r["results"]] == [1], r
    finally:
        llm.httpx.AsyncClient = orig
        llm.log.removeHandler(h)
    print("parse retry ok")


async def test_auth():
    """首次设置、未登录拦截、登录失败锁定、修改密码后旧会话失效。"""
    from app import auth
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        # 未登录：页面跳转到登录页，接口返回 401，登录页和健康检查可访问
        r = await c.get("/", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login.html", (r.status_code, r.headers)
        r = await c.get("/app.js", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("/login.html?next="), r.headers
        for path in ("/api/settings", "/api/project", "/api/config"):
            assert (await c.get(path)).status_code == 401, path
        assert (await c.post("/api/translate", json={"items": [{"id": 1, "text": "x"}]})).status_code == 401
        assert (await c.get("/login.html")).status_code == 200 and (await c.get("/api/health")).status_code == 200
        st = (await c.get("/api/auth/status")).json()
        assert st == {"configured": True, "authenticated": False, "username": ""}, st   # 之前的测试已完成设置
        # 不能重复设置
        assert (await c.post("/api/auth/setup", json={"username": "x", "password": "123456"})).status_code == 409
        # 伪造或篡改的 Cookie 无效
        c.headers["Cookie"] = "ais_session=abc.def"
        assert (await c.get("/api/settings")).status_code == 401
        del c.headers["Cookie"]
        # 登录成功
        token = await login(c)
        r = await c.get("/", follow_redirects=False)
        assert r.status_code == 200 and r.headers.get("cache-control") == "no-cache", r.headers
        # 首页里的脚本带内容版本号，部署后浏览器一定会取新文件
        import re as _re
        assert _re.search(r'src="app\.js\?v=[0-9a-f]{10}"', r.text) and _re.search(r'href="style\.css\?v=[0-9a-f]{10}"', r.text), r.text[:400]
        r = await c.get("/app.js?v=whatever")
        assert r.status_code == 200 and r.headers.get("cache-control") == "no-cache" and "parseShift" in r.text
        assert (await c.get("/api/auth/status")).json()["authenticated"] is True
        # 修改密码：旧密码错误被拒；成功后旧 Cookie 失效
        assert (await c.post("/api/auth/password", json={"old_password": "nope", "new_password": "abcdef"})).status_code == 401
        r = await c.post("/api/auth/password", json={"old_password": TEST_PASS, "new_password": "new-pass-456"})
        assert r.status_code == 200, r.text
        new_token = r.cookies.get("ais_session") or r.headers["set-cookie"].split("ais_session=")[1].split(";")[0]
        c.headers["Cookie"] = f"ais_session={token}"
        assert (await c.get("/api/settings")).status_code == 401, "旧会话应失效"
        c.headers["Cookie"] = f"ais_session={new_token}"
        assert (await c.get("/api/settings")).status_code == 200
        # 改回测试密码
        r = await c.post("/api/auth/password", json={"old_password": "new-pass-456", "new_password": TEST_PASS})
        assert r.status_code == 200
        del c.headers["Cookie"]
        # 连续输错 5 次后锁定，正确密码也要等
        for _ in range(5):
            assert (await c.post("/api/auth/login", json={"username": TEST_USER, "password": "wrong"})).status_code == 401
        r = await c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASS})
        assert r.status_code == 429 and "Retry-After" in r.headers, r.text
        auth._fails.clear()
        # 文件权限、密码不以明文保存
        f = auth.auth_file()
        assert oct(os.stat(f).st_mode & 0o777) == "0o600" and TEST_PASS not in f.read_text()
        # 退出登录
        await login(c)
        r = await c.post("/api/auth/logout")
        assert 'ais_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"], r.headers["set-cookie"]
    print("auth ok")


async def test_project():
    """字幕项目：整体替换、按条合并、按版本增量拉取、其他设备换文件时冲突。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await login(c)
        assert (await c.get("/api/project")).json() == {"version": 0, "project": None}
        cues = [{"id": i, "start": i * 1000, "end": i * 1000 + 900, "text": f"L{i}", "translation": "", "status": ""} for i in range(1, 6)]
        proj = {"project_id": "p1", "filename": "a.srt", "format": "srt", "header": "", "cues": cues}
        r = (await c.put("/api/project", json={"project": proj})).json()
        assert r == {"version": 1, "project_id": "p1"}, r
        full = (await c.get("/api/project")).json()
        assert full["full"] and full["project"]["filename"] == "a.srt" and len(full["project"]["cues"]) == 5
        # 设备 A 改第 1 条，设备 B 改第 2 条：互不覆盖
        a = (await c.patch("/api/project", json={"project_id": "p1", "cues": [{**cues[0], "translation": "甲", "status": "done"}]})).json()
        b = (await c.patch("/api/project", json={"project_id": "p1", "cues": [{**cues[1], "translation": "乙", "status": "edited"}]})).json()
        assert (a, b) == ({"version": 2, "prev_version": 1}, {"version": 3, "prev_version": 2}), (a, b)
        # A 增量拉取 version 2 之后的修改：只拿到 B 改的第 2 条
        ch = (await c.get("/api/project?since=2&pid=p1")).json()
        assert not ch["full"] and [x["id"] for x in ch["changed"]] == [2] and ch["changed"][0]["translation"] == "乙"
        # 状态只保存 done/error/edited
        await c.patch("/api/project", json={"project_id": "p1", "cues": [{**cues[2], "status": "pending"}]})
        got = (await c.get("/api/project")).json()["project"]["cues"]
        assert [x["translation"] for x in got[:2]] == ["甲", "乙"] and got[2]["status"] == ""
        # 其他设备打开了新文件 → 旧项目的 PATCH 返回 409
        await c.put("/api/project", json={"project": {**proj, "project_id": "p2", "filename": "b.srt"}})
        r = await c.patch("/api/project", json={"project_id": "p1", "cues": [cues[0]]})
        assert r.status_code == 409 and r.json()["project_id"] == "p2"
        # pid 不一致时返回整份
        assert (await c.get("/api/project?since=3&pid=p1")).json()["full"] is True
        # 错误输入
        assert (await c.put("/api/project", json={"project": {"project_id": "x", "cues": []}})).status_code == 400
        assert (await c.put("/api/project", json={"project": {"project_id": "x", "cues": [cues[0], cues[0]]}})).status_code == 400
        f = __import__("app.store", fromlist=["x"]).project_file()
        assert oct(os.stat(f).st_mode & 0o777) == "0o600"
    print("project ok")


if __name__ == "__main__":
    import tempfile
    from app import store as _store
    _tmp = tempfile.TemporaryDirectory()
    _store.configure(_tmp.name)   # 自检使用临时目录，不影响真实设置
    test_subtitles()
    test_parse_results()
    asyncio.run(test_api())
    test_thinking()
    test_layout_export()
    test_reflow_js()
    test_parse_tolerance()
    asyncio.run(test_robustness())
    asyncio.run(test_parse_retry())
    asyncio.run(test_settings_store())
    asyncio.run(test_auth())
    asyncio.run(test_project())
    asyncio.run(test_thinking_api())
    print("ALL TESTS PASSED")
