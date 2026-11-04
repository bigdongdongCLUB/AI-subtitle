"""思考深度：按服务商和模型给出可选档位，并把档位换成各家接口的参数。

档位（从浅到深）：off 关闭、on 开启（只能开关的模型）、low 轻度、medium 中度、high 重度、max 最高；
另有 default「不指定」：不发送任何思考参数，由模型自己决定。

各家规则依据官方文档，并用真实 Key 实测过 DeepSeek 与 Groq（2026-11）：
- DeepSeek V4：thinking.type 开关 + reasoning_effort（只有 low / high / max，medium 等同 high），默认 high。
- Groq：gpt-oss 只能 low / medium / high（不能关闭）；Qwen3 可 none / low / medium / high。
- OpenAI：推理模型用 reasoning_effort；gpt-5 最低 minimal，gpt-5.1 及以后可 none；o 系列 low / medium / high。
- OpenRouter：reasoning.effort，none 表示关闭。
- 硅基流动、阿里云百炼：enable_thinking 开关 + thinking_budget（思考 token 上限）。
- 智谱 GLM-4.5 及以后、Kimi K2.6：thinking.type 只能开或关；Kimi K3 用 reasoning_effort（low / high / max）。
- Ollama：reasoning_effort；gpt-oss 支持 low / medium / high，其他思考模型只能开或关。
服务商不接受所选参数（返回 400）时，会去掉参数按模型默认重试，并在服务器日志里记录。
"""
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

ORDER = ["off", "on", "low", "medium", "high", "max"]
ALL_LEVELS = set(ORDER) | {"default"}
LABELS = {"off": "关闭", "on": "开启", "low": "轻度", "medium": "中度", "high": "重度", "max": "最高", "default": "不指定"}
SLOW_LEVELS = {"high", "max"}
# 思考预算（token 上限），用于硅基流动、百炼
BUDGET = {"low": 1024, "medium": 4096, "high": 16384, "max": 32768}


def _spec(provider: str, levels: Dict[str, Dict[str, Any]], *, hints: Optional[Dict[str, str]] = None,
          note: str = "", model_default: Optional[str] = None, temp_ignored: bool = False,
          default_hint: str = "") -> Dict[str, Any]:
    """levels：{档位: 要发送的参数}，按 ORDER 排序；总会附加 default（不发送参数）。"""
    lv = {k: levels[k] for k in ORDER if k in levels}
    lv["default"] = {}
    return {"provider": provider, "levels": lv, "hints": hints or {}, "note": note,
            "model_default": model_default, "temp_ignored": temp_ignored, "default_hint": default_hint}


def _none(provider: str, note: str) -> Dict[str, Any]:
    return _spec(provider, {}, note=note)


def _effort(values: Dict[str, Optional[str]], key: str = "reasoning_effort") -> Dict[str, Dict[str, Any]]:
    return {lvl: {key: v} for lvl, v in values.items()}


def _budget(off_param: Optional[Dict[str, Any]], on_param: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if off_param is not None:
        out["off"] = off_param
    for lvl, n in BUDGET.items():
        out[lvl] = {**on_param, "thinking_budget": n}
    return out


BUDGET_HINTS = {"low": "约 1K token", "medium": "约 4K token", "high": "约 16K token", "max": "约 32K token"}


def describe(base_url: str, model: str) -> Dict[str, Any]:
    base_url = (base_url or os.getenv("LLM_BASE_URL") or "").strip()
    u = urlparse(base_url)
    host = (u.hostname or "").lower()
    m = (model or "").strip().lower()
    name = m.split("/")[-1]

    # ---------------- DeepSeek 官方
    if host.endswith("deepseek.com"):
        if name == "deepseek-chat":
            return _none("DeepSeek", "deepseek-chat 是旧名称，固定对应 V4 的非思考模式。需要思考请改用 deepseek-v4-pro 或 deepseek-flash。")
        dsk = {"low": "low", "high": "high", "max": "max"}
        levels = {k: {"thinking": {"type": "enabled"}, "reasoning_effort": v} for k, v in dsk.items()}
        if name != "deepseek-reasoner":
            levels["off"] = {"thinking": {"type": "disabled"}}
        return _spec("DeepSeek", levels, model_default="high", temp_ignored=True,
                     hints={"off": "最快，翻译推荐", "low": "low", "high": "high", "max": "max"},
                     note="DeepSeek 只有 low、high、max 三档思考强度（medium 等同 high）。开启思考时温度不起作用。")

    # ---------------- Groq
    if host.endswith("groq.com"):
        if "gpt-oss" in name:
            return _spec("Groq", _effort({"low": "low", "medium": "medium", "high": "high"}), model_default="medium",
                         hints={"low": "low，最快", "medium": "medium", "high": "high"},
                         note="gpt-oss 无法完全关闭思考，最低是轻度（low）。")
        if "muse" in name:
            return _spec("Groq", _effort({"low": "low", "medium": "medium", "high": "high", "max": "xhigh"}), model_default="max",
                         hints={"max": "xhigh"}, note="该模型始终思考，无法关闭。")
        if name.startswith("qwen3"):
            return _spec("Groq", _effort({"off": "none", "low": "low", "medium": "medium", "high": "high"}),
                         hints={"off": "最快，翻译推荐"}, default_hint="Groq 上默认不思考",
                         note="Groq 上的 Qwen3 不设置时默认不思考。")
        return _none("Groq", "该模型不是推理模型，没有思考深度可调。")

    # ---------------- OpenAI 官方
    if host.endswith("openai.com"):
        if re.match(r"^o[1-9]", name):
            return _spec("OpenAI", _effort({"low": "low", "medium": "medium", "high": "high"}), model_default="medium",
                         note="o 系列推理模型无法关闭思考，最低是轻度（low）。")
        mm = re.match(r"^gpt-(\d+)(?:\.(\d+))?", name)
        if mm and int(mm.group(1)) >= 5 and "chat" not in name:
            first = int(mm.group(1)) == 5 and not mm.group(2)      # gpt-5 / gpt-5-mini / gpt-5-nano
            off = "minimal" if first else "none"
            return _spec("OpenAI", _effort({"off": off, "low": "low", "medium": "medium", "high": "high"}), model_default="medium",
                         hints={"off": off}, note="个别模型不支持某一档时，会自动改用模型默认，并记在服务器日志里。")
        return _none("OpenAI", "该模型不是推理模型，没有思考深度可调。")

    # ---------------- OpenRouter
    if host.endswith("openrouter.ai"):
        return _spec("OpenRouter", _effort({"off": "none", "low": "low", "medium": "medium", "high": "high"}, key="__or__"),
                     note="只对支持推理的模型生效；模型不支持某一档时会自动改用模型默认。")

    # ---------------- 硅基流动
    if "siliconflow" in host:
        if re.search(r"(^|[-/])r1\b|deepseek-r1|qwq|thinking", m):
            return _spec("硅基流动", _budget(None, {}), hints=BUDGET_HINTS, model_default="medium",
                         note="该模型始终思考，档位控制思考长度上限（thinking_budget）。")
        if _hybrid_siliconflow(m):
            return _spec("硅基流动", _budget({"enable_thinking": False}, {"enable_thinking": True}), hints=BUDGET_HINTS,
                         model_default="medium", note="档位控制思考长度上限（thinking_budget）。硅基流动上这类模型默认开启思考。")
        return _none("硅基流动", "该模型不是推理模型，没有思考深度可调。")

    # ---------------- 阿里云百炼
    if host.endswith("aliyuncs.com"):
        if re.search(r"qwq|deepseek-r1|thinking", m):
            return _spec("阿里云百炼", _budget(None, {}), hints=BUDGET_HINTS,
                         note="该模型始终思考，档位控制思考长度上限（thinking_budget）。")
        if _hybrid_dashscope(m):
            return _spec("阿里云百炼", _budget({"enable_thinking": False}, {"enable_thinking": True}), hints=BUDGET_HINTS,
                         note="档位控制思考长度上限（thinking_budget）。qwen-plus / qwen-flash 默认不思考，Qwen3.5 及以后默认思考。")
        return _none("阿里云百炼", "该模型不是推理模型，没有思考深度可调。")

    # ---------------- 智谱
    if host.endswith("bigmodel.cn") or host.endswith("z.ai"):
        if re.match(r"^glm-5\.3", name):
            return _none("智谱", "GLM-5.3 强制思考，不能关闭，也没有深度可调。")
        if re.match(r"^glm-(4\.[5-9]|5)", name):
            return _spec("智谱", {"off": {"thinking": {"type": "disabled"}}, "on": {"thinking": {"type": "enabled"}}},
                         model_default="on", note="智谱只能开启或关闭思考，没有深度档位。")
        return _none("智谱", "该模型不是推理模型，没有思考深度可调。")

    # ---------------- Kimi（月之暗面）
    if host.endswith("moonshot.cn") or host.endswith("moonshot.ai") or host.endswith("kimi.com"):
        if name.startswith("kimi-k3"):
            return _spec("Kimi", _effort({"low": "low", "high": "high", "max": "max"}), model_default="max",
                         note="K3 系列始终思考，可选 low、high、max 三档。")
        if name.startswith("kimi-k2.6"):
            return _spec("Kimi", {"off": {"thinking": {"type": "disabled"}}, "on": {"thinking": {"type": "enabled"}}},
                         model_default="on", note="K2.6 只能开启或关闭思考。")
        if "thinking" in name or name.startswith("kimi-k2.7-code"):
            return _none("Kimi", "该模型始终思考，无法调节。")
        return _none("Kimi", "该模型不是推理模型，没有思考深度可调。")

    # ---------------- Ollama 本地
    if u.port == 11434 or "ollama" in host:
        if "gpt-oss" in name:
            return _spec("Ollama", _effort({"off": "none", "low": "low", "medium": "medium", "high": "high"}),
                         note="gpt-oss 支持 low、medium、high 三档。")
        if re.search(r"qwen3|deepseek-r1|qwq|magistral|deepseek-v3\.[1-9]|gpt-oss|cogito|exaone-deep", name):
            return _spec("Ollama", _effort({"off": "none", "on": "high"}), note="该模型只能开启或关闭思考。")
        return _none("Ollama", "该模型不是思考模型，或 Ollama 未标注它支持思考。")

    # ---------------- 其他 OpenAI 兼容服务
    return _spec("通用", _effort({"off": "none", "low": "low", "medium": "medium", "high": "high"}),
                 note="使用通用参数 reasoning_effort。服务商不支持时会自动忽略，按模型默认处理。")


def _hybrid_siliconflow(m: str) -> bool:
    if re.search(r"instruct|coder|embedding|reranker", m):
        return False
    return bool(re.search(r"qwen3|hunyuan-a13b|glm-4\.5|glm-4\.6|glm-5|deepseek-v3\.[1-9]", m))


def _hybrid_dashscope(m: str) -> bool:
    if re.search(r"instruct|coder|embedding|reranker|-vl-", m):
        return False
    return bool(re.search(r"^qwen-(plus|flash|turbo)|qwen3|deepseek-v3\.[1-9]|deepseek-v4|glm-4\.[5-9]|glm-5", m))


def resolve(spec: Dict[str, Any], level: Optional[str]) -> str:
    """把保存的档位换成该模型实际可用的档位。"""
    levels = spec["levels"]
    level = level if level in ALL_LEVELS else "off"
    if level in levels:
        return level
    real = [k for k in ORDER if k in levels]
    if not real:
        return "default"
    if level == "default":
        return "default"
    if level == "off":
        return real[0]                      # 不能关闭：用最浅的一档
    idx = ORDER.index(level)
    return min(real, key=lambda k: (abs(ORDER.index(k) - idx), -ORDER.index(k)))


def params(base_url: str, model: str, level: Optional[str]) -> Dict[str, Any]:
    spec = describe(base_url, model)
    p = spec["levels"][resolve(spec, level)]
    if "__or__" in p:                       # OpenRouter：reasoning.effort
        return {"reasoning": {"effort": p["__or__"]}}
    return dict(p)


def options(base_url: str, model: str, current: Optional[str] = None) -> Dict[str, Any]:
    """给前端用：可选档位列表、当前值实际对应的档位、说明。"""
    spec = describe(base_url, model)
    items: List[Dict[str, str]] = []
    real_levels = [k for k in ORDER if k in spec["levels"]]
    for k in spec["levels"]:
        if k == "default":
            if not real_levels:
                label = "不支持调节"
            else:
                label = LABELS["default"] + f"（{spec['default_hint'] or '由模型决定'}）"
        else:
            extra = [x for x in (spec["hints"].get(k, ""), "模型默认" if spec["model_default"] == k else "") if x]
            label = LABELS[k] + (f"（{'，'.join(extra)}）" if extra else "")
        items.append({"id": k, "label": label})
    real = [k for k in ORDER if k in spec["levels"]]
    recommended = real[0] if real else "default"
    return {
        "provider": spec["provider"],
        "levels": items,
        "resolved": resolve(spec, current) if current is not None else recommended,
        "recommended": recommended,
        "adjustable": bool(real),
        "note": spec["note"],
        "slow": sorted(SLOW_LEVELS & set(real)) + (["default"] if spec["model_default"] in SLOW_LEVELS else []),
        "temp_ignored": [k for k in spec["levels"] if spec["temp_ignored"] and k != "off"],
    }
