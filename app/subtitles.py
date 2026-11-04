"""字幕文件解析与导出（内置实现，支持 SRT / WebVTT / ASS / SSA，无第三方依赖）。"""
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from charset_normalizer import from_bytes as _cn_from_bytes
except Exception:  # pragma: no cover
    _cn_from_bytes = None

EXPORT_FORMATS = {"srt", "vtt", "ass", "ssa"}

# ---------------------------------------------------------------- 编码
def decode_bytes(data: bytes) -> str:
    """尽量正确地把字幕文件解码为字符串。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # 非 UTF-8 时优先按 GB18030（简体中文最常见）尝试；短文本下自动检测并不可靠
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError:
        pass
    if _cn_from_bytes is not None:
        best = _cn_from_bytes(data).best()
        if best is not None:
            return str(best)
    for enc in ("big5", "shift_jis", "euc-kr", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 时间
_TS_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,:](\d{1,3})")


def parse_timestamp(s: str) -> Optional[int]:
    """'01:02:03,456' / '02:03.456' / '1:02:03.45' -> 毫秒"""
    m = _TS_RE.search(s.strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi, se = int(m.group(2)), int(m.group(3))
    frac = m.group(4)
    ms = int(frac.ljust(3, "0")) if len(frac) <= 3 else int(frac[:3])
    return ((h * 60 + mi) * 60 + se) * 1000 + ms


def ms_to_srt(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, f = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{f:03d}"


def ms_to_vtt(ms: int) -> str:
    return ms_to_srt(ms).replace(",", ".")


def ms_to_ass(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, f = divmod(rem, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{f // 10:02d}"


# ---------------------------------------------------------------- 格式识别
def detect_format(text: str, ext: str = "") -> str:
    head = text.lstrip()[:4000]
    if re.search(r"^\[Script Info\]", head, re.M) or re.search(r"^\[V4\+? Styles\]", head, re.M) or re.search(r"^Dialogue:", head, re.M):
        if ext == "ssa" or re.search(r"^\[V4 Styles\]", head, re.M):
            return "ssa"
        return "ass"
    if head.startswith("WEBVTT"):
        return "vtt"
    if re.search(r"\d{1,2}:\d{2}:\d{2},\d{3}\s*-->", head):
        return "srt"
    if re.search(r"\d{1,2}:\d{2}(?::\d{2})?\.\d{3}\s*-->", head):
        return "vtt"
    if ext in ("srt", "vtt", "ass", "ssa"):
        return ext
    return "srt"


# ---------------------------------------------------------------- SRT / VTT
_ARROW_RE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)(.*)$")
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_srt_vtt(text: str, is_vtt: bool) -> List[Dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: List[Dict[str, Any]] = []
    i, n = 0, len(lines)
    # 跳过 WEBVTT 头部块
    if is_vtt:
        while i < n and lines[i].strip():
            i += 1
    while i < n:
        # 跳过空行
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break
        block: List[str] = []
        while i < n and lines[i].strip():
            block.append(lines[i])
            i += 1
        # VTT 的 NOTE / STYLE / REGION 块跳过
        if is_vtt and block and re.match(r"^(NOTE|STYLE|REGION)\b", block[0].strip()):
            continue
        # 找到时间行
        t_idx = next((k for k, ln in enumerate(block) if "-->" in ln), None)
        if t_idx is None:
            continue
        m = _ARROW_RE.match(block[t_idx])
        if not m:
            continue
        start, end = parse_timestamp(m.group(1)), parse_timestamp(m.group(2))
        if start is None or end is None:
            continue
        body_lines = block[t_idx + 1 :]
        body = "\n".join(body_lines).strip()
        meta: Dict[str, Any] = {}
        if is_vtt:
            settings = m.group(3).strip()
            if settings:
                meta["vtt_settings"] = settings
            if t_idx > 0:
                meta["vtt_id"] = block[t_idx - 1].strip()
        cues.append({"start": start, "end": end, "text": body, "meta": meta})
    return cues


# ---------------------------------------------------------------- ASS / SSA
_ASS_EVENT_FIELDS = ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect", "Text"]
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def _parse_ass(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """返回 (去掉事件的头部文本, 事件列表)"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header_lines: List[str] = []
    cues: List[Dict[str, Any]] = []
    section = ""
    fmt_fields: List[str] = []
    for raw in lines:
        line = raw.strip("﻿")
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
            header_lines.append(line)
            continue
        if section == "[events]":
            if s.lower().startswith("format:"):
                fmt_fields = [f.strip() for f in s.split(":", 1)[1].split(",")]
                header_lines.append(line)
                continue
            low = s.lower()
            if low.startswith("dialogue:") or low.startswith("comment:"):
                kind = "Dialogue" if low.startswith("dialogue:") else "Comment"
                fields = fmt_fields or _ASS_EVENT_FIELDS
                values = s.split(":", 1)[1].strip().split(",", len(fields) - 1)
                if len(values) < len(fields):
                    values += [""] * (len(fields) - len(values))
                rec = dict(zip(fields, [v.strip() if f != "Text" else v for f, v in zip(fields, values)]))
                start = parse_timestamp(rec.get("Start", "")) or 0
                end = parse_timestamp(rec.get("End", "")) or 0
                raw_text = rec.get("Text", "")
                plain = _ASS_TAG_RE.sub("", raw_text).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ").strip()
                meta = {k: v for k, v in rec.items() if k not in ("Start", "End", "Text")}
                meta["kind"] = kind
                cues.append({"start": start, "end": end, "text": plain, "raw": raw_text, "meta": meta})
            # 事件区的其他行（空行等）不进 header
            continue
        header_lines.append(line)
    # 保证头部有 Events 段及 Format 行
    header = "\n".join(header_lines).rstrip() + "\n"
    if "[events]" not in header.lower():
        header += "\n[Events]\nFormat: " + ", ".join(_ASS_EVENT_FIELDS) + "\n"
    elif not fmt_fields:
        header = header.replace("[Events]", "[Events]\nFormat: " + ", ".join(_ASS_EVENT_FIELDS))
    return header, cues


DEFAULT_ASS_HEADER = """[Script Info]
; Script generated by AI Subtitle Studio
ScriptType: v4.00+
PlayResX: {play_x}
PlayResY: {play_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{color},&H000000FF,{outline_color},&H80000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin},{margin},{margin},1
Style: Secondary,{font2},{font_size2},{color2},&H000000FF,{outline_color2},&H80000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin},{margin},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ---------------------------------------------------------------- 对外接口
def parse_subtitle(data: bytes, filename: str = "") -> Dict[str, Any]:
    """把上传的字幕文件解析成统一的 JSON 结构。"""
    text = decode_bytes(data)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    fmt = detect_format(text, ext)
    header = ""
    if fmt in ("ass", "ssa"):
        header, raw_cues = _parse_ass(text)
    else:
        raw_cues = _parse_srt_vtt(text, fmt == "vtt")
    if not raw_cues:
        raise ValueError("没有解析到任何字幕条目，请确认文件格式")

    cues: List[Dict[str, Any]] = []
    for i, c in enumerate(raw_cues):
        cue: Dict[str, Any] = {
            "id": i + 1,
            "start": int(c["start"]),
            "end": int(c["end"]),
            "text": c["text"],
            "translation": "",
        }
        if c.get("meta"):
            cue["meta"] = c["meta"]
        if c.get("raw") is not None:
            cue["raw"] = c["raw"]
        cues.append(cue)

    return {"filename": filename, "format": fmt, "header": header, "count": len(cues), "cues": cues}


def _choose_parts(cue: Dict[str, Any], content: str) -> List[Tuple[str, bool]]:
    """返回按显示顺序排列的 [(文本, 是否为双语里的原文)]。"""
    orig = _norm_nl(cue.get("text") or "")
    trans = _norm_nl(cue.get("translation") or "")
    if content == "original":
        parts = [(orig, False)]
    elif content == "bilingual_to":  # 译文在上
        parts = [(trans, False), (orig, True)] if trans else [(orig, False)]
    elif content == "bilingual_ot":  # 原文在上
        parts = [(orig, True), (trans, False)] if trans else [(orig, False)]
    else:                            # translation（无译文则用原文）
        parts = [(trans or orig, False)]
    return [(t, sec) for t, sec in parts if t]


def _choose_text(cue: Dict[str, Any], content: str) -> str:
    return "\n".join(t for t, _ in _choose_parts(cue, content))


def _norm_nl(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


# 导出分辨率（画面高度）→ ASS 画布宽度
PLAY_RES = {720: 1280, 1080: 1920, 2160: 3840}


# ---------------------------------------------------------------- ASS 样式（字体、颜色）
# 主字幕：译文，或双语原文件的第一种语言；副字幕：双语导出时的原文，或双语原文件的第二种语言。
DEFAULT_STYLE = {"font": "Arial", "color": "#FFFFFF", "outline_color": "#000000",
                 "font2": "Arial", "color2": "#FFFFFF", "outline_color2": "#000000"}
SECONDARY_STYLE = "Secondary"
_FONT_BAD = re.compile(r"[,{}\\\r\n\t]")
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
STYLE_SECTIONS = ("[v4+ styles]", "[v4 styles]", "[v4 styles+]")


def clean_style(style: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """校验前端传来的样式：字体名去掉 ASS 里的分隔符，颜色只接受 #RRGGBB；无效的用默认值。"""
    st = style or {}
    out = dict(DEFAULT_STYLE)
    for k in ("font", "font2"):
        v = _FONT_BAD.sub("", str(st.get(k) or "")).strip()[:64]
        if v:
            out[k] = v
    for k in ("color", "outline_color", "color2", "outline_color2"):
        v = str(st.get(k) or "").strip()
        if _HEX_COLOR.fullmatch(v):
            out[k] = v.upper()
    return out


def hex_to_ass(h: str, alpha: str = "00") -> str:
    """#RRGGBB → ASS 的 &HAABBGGRR"""
    return f"&H{alpha}{h[5:7]}{h[3:5]}{h[1:3]}".upper()


def ass_to_hex(v: str) -> Tuple[Optional[str], str, bool]:
    """ASS 颜色 → (#RRGGBB, 透明度 AA, 是否为十六进制写法)。也支持 SSA 的十进制写法。"""
    v = (v or "").strip()
    try:
        if v.upper().startswith("&H"):
            n = int(v[2:].rstrip("&") or "0", 16)
            hexform = True
        else:
            n = int(v)
            hexform = False
    except ValueError:
        return None, "00", True
    a, b, g, r = (n >> 24) & 255, (n >> 16) & 255, (n >> 8) & 255, n & 255
    return f"#{r:02X}{g:02X}{b:02X}", f"{a:02X}", hexform


def _ass_color_like(old: str, new_hex: str) -> str:
    """按原来的写法（十六进制或十进制）写入新颜色，并保留原来的透明度。"""
    _, alpha, hexform = ass_to_hex(old)
    if hexform:
        return hex_to_ass(new_hex, alpha)
    r, g, b = int(new_hex[1:3], 16), int(new_hex[3:5], 16), int(new_hex[5:7], 16)
    return str((int(alpha, 16) << 24) | (b << 16) | (g << 8) | r)


def _default_header(font_size: int, play_res: int, style: Dict[str, str], secondary_scale: int) -> str:
    """按分辨率生成样式：描边、阴影、边距随画面高度等比换算；副字幕按比例缩小。"""
    k = play_res / 1080
    num = lambda v: (f"{v:.1f}".rstrip("0").rstrip(".") if v % 1 else str(int(v)))
    rep = {
        "{play_x}": str(PLAY_RES[play_res]), "{play_y}": str(play_res),
        "{font_size}": str(font_size), "{font_size2}": str(max(1, round(font_size * secondary_scale / 100))),
        "{font}": style["font"], "{font2}": style["font2"],
        "{color}": hex_to_ass(style["color"]), "{outline_color}": hex_to_ass(style["outline_color"]),
        "{color2}": hex_to_ass(style["color2"]), "{outline_color2}": hex_to_ass(style["outline_color2"]),
        "{outline}": num(round(2.5 * k, 1)), "{shadow}": num(round(1 * k, 1)), "{margin}": str(round(30 * k)),
    }
    out = DEFAULT_ASS_HEADER.strip()
    for a, b in rep.items():
        out = out.replace(a, b)
    return out


def ass_main_style(header: str, cues: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """导入的 ASS 里的「主要样式」（对白里用得最多的样式，其次 Default，再次第一个样式）：
    返回 {name, font, size, color, outline_color}，颜色为 #RRGGBB。"""
    styles = _ass_style_rows(header)
    if not styles:
        return None
    counts: Dict[str, int] = {}
    for c in cues:
        meta = c.get("meta") or {}
        if meta.get("kind", "Dialogue") == "Dialogue" and meta.get("Style"):
            counts[meta["Style"]] = counts.get(meta["Style"], 0) + 1
    name = next((n for n in sorted(counts, key=lambda n: -counts[n]) if n in styles), None)
    if name is None:
        name = "Default" if "Default" in styles else next(iter(styles))
    row = styles[name]
    oc = "outlinecolour" if "outlinecolour" in row else "tertiarycolour"
    try:
        size = float(row.get("fontsize", ""))
    except ValueError:
        size = None
    return {"name": name, "font": row.get("fontname", ""), "size": size,
            "color": ass_to_hex(row.get("primarycolour", ""))[0], "outline_color": ass_to_hex(row.get(oc, ""))[0]}


def _ass_style_rows(header: str) -> Dict[str, Dict[str, str]]:
    """读取样式表：{样式名: {字段名(小写): 值}}。"""
    out: Dict[str, Dict[str, str]] = {}
    fmt: List[str] = []
    in_styles = False
    for line in (header or "").splitlines():
        t = line.strip()
        if t.startswith("["):
            in_styles = t.lower() in STYLE_SECTIONS
            continue
        if not in_styles:
            continue
        if t.lower().startswith("format:"):
            fmt = [f.strip().lower() for f in t.split(":", 1)[1].split(",")]
        elif t.lower().startswith("style:") and fmt:
            vals = [v.strip() for v in t.split(":", 1)[1].split(",")]
            if "name" in fmt and len(vals) >= len(fmt):
                row = dict(zip(fmt, vals))
                out[row["name"]] = row
    return out


def _edit_imported_header(header: str, cues: List[Dict[str, Any]], font_size: int,
                          style: Optional[Dict[str, str]], secondary_scale: int) -> Tuple[str, str]:
    """导入的 ASS：沿用它的画布和其他样式。
    - 所有样式的字号按「主要样式」的新字号等比缩放；
    - style 不为空时，把主要样式的字体、文字色、描边色改成所选的（保留原来的透明度）；
    - 在主要样式后面加一个副字幕样式（字体、颜色按所选，字号按比例）。
    返回 (新的头部, 副字幕样式名)。"""
    main = ass_main_style(header, cues)
    if not main:
        return header, SECONDARY_STYLE
    ratio = font_size / main["size"] if main["size"] else 1.0
    names = set(_ass_style_rows(header))
    sec_name = SECONDARY_STYLE if SECONDARY_STYLE not in names else SECONDARY_STYLE + "_AIS"
    st = style or {}
    lines: List[str] = []
    fmt: List[str] = []
    in_styles = False
    for line in header.splitlines():
        t = line.strip()
        if t.startswith("["):
            in_styles = t.lower() in STYLE_SECTIONS
        elif in_styles and t.lower().startswith("format:"):
            fmt = [f.strip().lower() for f in t.split(":", 1)[1].split(",")]
        elif in_styles and t.lower().startswith("style:") and fmt:
            prefix, rest = line.split(":", 1)
            vals = [v.strip() for v in rest.split(",")]
            if len(vals) >= len(fmt):
                row = dict(zip(fmt, vals))
                idx = {k: i for i, k in enumerate(fmt)}
                oc = "outlinecolour" if "outlinecolour" in idx else ("tertiarycolour" if "tertiarycolour" in idx else None)
                if "fontsize" in idx and abs(ratio - 1) >= 0.005:
                    try:
                        vals[idx["fontsize"]] = str(max(1, round(float(vals[idx["fontsize"]]) * ratio)))
                    except ValueError:
                        pass
                if row.get("name") == main["name"]:
                    if st.get("font") and "fontname" in idx:
                        vals[idx["fontname"]] = st["font"]
                    if st.get("color") and "primarycolour" in idx:
                        vals[idx["primarycolour"]] = _ass_color_like(row["primarycolour"], st["color"])
                    if st.get("outline_color") and oc:
                        vals[idx[oc]] = _ass_color_like(row[oc], st["outline_color"])
                    lines.append(prefix + ": " + ",".join(vals))
                    # 副字幕样式：在主要样式基础上改名、字体、颜色、字号
                    sec = list(vals)
                    sec[idx["name"]] = sec_name
                    if "fontsize" in idx:
                        try:
                            sec[idx["fontsize"]] = str(max(1, round(float(vals[idx["fontsize"]]) * secondary_scale / 100)))
                        except ValueError:
                            pass
                    if st.get("font2") and "fontname" in idx:
                        sec[idx["fontname"]] = st["font2"]
                    if st.get("color2") and "primarycolour" in idx:
                        sec[idx["primarycolour"]] = _ass_color_like(row["primarycolour"], st["color2"])
                    if st.get("outline_color2") and oc:
                        sec[idx[oc]] = _ass_color_like(row[oc], st["outline_color2"])
                    lines.append(prefix + ": " + ",".join(sec))
                    continue
                line = prefix + ": " + ",".join(vals)
        lines.append(line)
    return "\n".join(lines), sec_name


def ass_main_style_size(header: str, cues: List[Dict[str, Any]]) -> Optional[float]:
    """导入的 ASS 里「主要样式」的字号。"""
    m = ass_main_style(header, cues)
    return m["size"] if m else None


def build_subtitle(
    cues: List[Dict[str, Any]],
    fmt: str = "srt",
    content: str = "translation",
    header: str = "",
    font_size: int = 44,
    secondary_scale: int = 75,
    play_res: int = 1080,
    style: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """根据 JSON 结构生成字幕文件文本，返回 (文本, 扩展名)。
    play_res：画面高度 720 / 1080 / 2160，决定新生成 ASS 的画布大小；
    font_size：字号（按 play_res 画布计）。导入的 ASS 沿用自己的画布，字号改动按比例缩放它的全部样式；
    secondary_scale：ASS 副字幕（双语时的原文）的相对大小（%）；
    style：ASS 主/副字幕的字体和颜色，见 DEFAULT_STYLE。导入的 ASS 只在传了 style 时修改它的主要样式。"""
    fmt = (fmt or "srt").lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}")
    play_res = int(play_res or 1080) if int(play_res or 1080) in PLAY_RES else 1080
    font_size = max(10, min(240, int(font_size or 44)))
    secondary_scale = max(40, min(100, int(secondary_scale or 75)))
    imported = bool(header and header.strip())
    if fmt in ("ass", "ssa"):
        if imported:
            head, sec_name = _edit_imported_header(header.strip(), cues, font_size,
                                                   clean_style(style) if style else None, secondary_scale)
        else:
            head, sec_name = _default_header(font_size, play_res, clean_style(style), secondary_scale), SECONDARY_STYLE

    items = []
    for cue in cues:
        parts = _choose_parts(cue, content)
        if not parts:
            continue
        if fmt in ("srt", "vtt") and (cue.get("meta") or {}).get("kind") == "Comment":
            continue
        if fmt in ("ass", "ssa"):
            # 副字幕：{\rSecondary} 切换到副字幕样式（字体、颜色、字号），{\r} 恢复这一行原来的样式
            segs = []
            for t, sec in parts:
                t = t.replace("\n", "\\N")
                if sec:
                    t = f"{{\\r{sec_name}}}{t}{{\\r}}"
                segs.append(t)
            body = "\\N".join(segs)
        else:
            body = "\n".join(t for t, _ in parts)
        items.append((int(cue["start"]), int(cue["end"]), body, cue.get("meta") or {}))

    if fmt == "srt":
        out = []
        for i, (st, en, body, _) in enumerate(items, 1):
            out.append(f"{i}\n{ms_to_srt(st)} --> {ms_to_srt(en)}\n{body}\n")
        return "\n".join(out), "srt"

    if fmt == "vtt":
        out = ["WEBVTT\n"]
        for i, (st, en, body, meta) in enumerate(items, 1):
            settings = (" " + meta["vtt_settings"]) if meta.get("vtt_settings") else ""
            ident = (meta.get("vtt_id") or str(i)) + "\n"
            out.append(f"{ident}{ms_to_vtt(st)} --> {ms_to_vtt(en)}{settings}\n{body}\n")
        return "\n".join(out), "vtt"

    # ass / ssa：头部（样式）已在前面生成
    # 从头部读取 Format 行，决定字段顺序
    m = re.search(r"^\[Events\].*?^Format:\s*(.+)$", head, re.S | re.M | re.I)
    fields = [f.strip() for f in m.group(1).split(",")] if m else _ASS_EVENT_FIELDS
    lines = [head]
    for st, en, body, meta in items:
        rec = {"Start": ms_to_ass(st), "End": ms_to_ass(en), "Text": body}
        for f in fields:
            if f not in rec:
                v = meta.get(f)
                if v is None or v == "":
                    v = {"Layer": "0", "Style": "Default", "MarginL": "0", "MarginR": "0", "MarginV": "0"}.get(f, "")
                rec[f] = str(v)
        kind = meta.get("kind", "Dialogue")
        lines.append(f"{kind}: " + ",".join(rec[f] for f in fields))
    text = "\n".join(lines).rstrip("\n") + "\n"
    return text, fmt
