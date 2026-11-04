/* 字幕排版：把多行字幕合并为一行，超过安全宽度时再按断句位置换行。
 * 浏览器里挂在 window.SubReflow；Node 里可 require（用于自测）。
 *
 * 宽度按半角计：汉字、假名、谚文和全角符号算 2，其他字符算 1。
 * 合并规则：前一行以全角标点结尾时直接相连，否则用一个空格隔开（保留断句）；
 *           以「-」开头的多人对话行保留「-」，换行时优先在说话人之间断开。
 * 换行规则：先求最少行数，再在这个行数下让各行宽度尽量均衡，
 *           并优先在说话人之间、句末标点、逗号、空格处断开，不拆英文单词。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.SubReflow = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const TAG_RE = /<\/?[a-zA-Z][^>]*>|\{\\[^}]*\}/g;              // <i> </i> <font …> {\an8}
  const TAG_AT_RE = /^(<\/?[a-zA-Z][^>]*>|\{\\[^}]*\})/;
  const DASH_RE = /^[-–—－]/;                                     // 对话行开头的 -
  const FW_PUNCT = new Set('，。！？；：、…）」』》】〉〕｝］”’·～'.split(''));   // 之后不必再加空格
  const SENT_END = new Set('。！？!?.…'.split(''));
  const CLAUSE = new Set('，、；：,;:'.split(''));
  const NO_LINE_START = new Set('，。！？；：、…）」』》】〉〕｝］”’,.!?;:)]}%'.split(''));
  const NO_LINE_END = new Set('（「『《【〈〔｛［“‘([{'.split(''));

  function isWide(ch) {
    const c = ch.codePointAt(0);
    return (c >= 0x1100 && c <= 0x115F) || (c >= 0x2E80 && c <= 0xA4CF) || (c >= 0xAC00 && c <= 0xD7A3) ||
      (c >= 0xF900 && c <= 0xFAFF) || (c >= 0xFE30 && c <= 0xFE4F) || (c >= 0xFF00 && c <= 0xFF60) ||
      (c >= 0xFFE0 && c <= 0xFFE6) || (c >= 0x20000 && c <= 0x3FFFD);
  }
  function charWidth(ch) { return isWide(ch) ? 2 : 1; }
  function visible(s) { return String(s || '').replace(TAG_RE, ''); }
  function textWidth(s) { let w = 0; for (const ch of visible(s)) w += charWidth(ch); return w; }

  // ---------------------------------------------------------------- 合并
  function merge(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n').map(s => s.trim()).filter(Boolean);
    if (lines.length <= 1) return (lines[0] || '').replace(/[ \t]{2,}/g, ' ');
    let out = lines[0];
    for (let i = 1; i < lines.length; i++) {
      const next = lines[i];
      const prevVis = visible(out).trimEnd();
      const last = Array.from(prevVis).pop() || '';
      const nextVis = visible(next).trimStart();
      const speaker = DASH_RE.test(nextVis);
      // 全角标点后接中日韩文字时直接相连；接英文等时仍用空格隔开（双语字幕「我知道，\nI know」）
      const nextWide = nextVis ? isWide(Array.from(nextVis)[0]) : false;
      out += (!speaker && FW_PUNCT.has(last) && nextWide) ? next : ' ' + next;
    }
    return out.replace(/[ \t]{2,}/g, ' ');
  }

  // ---------------------------------------------------------------- 换行
  // 把文本拆成单元：标签（宽度 0，不可拆）或单个字符
  function units(text) {
    const out = [];
    let i = 0;
    while (i < text.length) {
      const m = text.slice(i).match(TAG_AT_RE);
      if (m) { out.push({ s: m[0], w: 0, tag: true }); i += m[0].length; continue; }
      const ch = String.fromCodePoint(text.codePointAt(i));
      out.push({ s: ch, w: charWidth(ch), tag: false });
      i += ch.length;
    }
    return out;
  }

  // 断点质量：数值越小越好
  const Q_SPEAKER = 0, Q_SENT = 1, Q_CLAUSE = 2, Q_CJK_SPACE = 2, Q_SPACE = 3, Q_CJK = 10;
  const Q_BEFORE_FUNC = 2.5, Q_NAME = 5, Q_BAD_END = 6;
  // 英文断行习惯：不在冠词、介词、物主代词后断开；尽量在连词、介词前断开；不拆开连续的大写单词（人名、地名）
  const BAD_END = new Set('a an the to of in on at for with from by into onto about as my your his her its our their and or but nor'.split(' '));
  const GOOD_START = new Set('and but or so because when while if that which who whom whose where to for with at in on of from about as than until after before since'.split(' '));
  const BALANCE = 0.1;   // 各行等宽的权重：自然断句比绝对等宽更重要
  const CLOSERS = new Set('"\'”’」』）)]》】'.split(''));

  function candidates(u) {
    const vis = []; // 可见字符在 u 中的下标
    u.forEach((x, i) => { if (!x.tag) vis.push(i); });
    const prevVisible = (i) => { for (let k = i - 1; k >= 0; k--) if (!u[k].tag) return u[k].s; return ''; };
    // 判断句末/逗号时越过收尾的引号、括号：say "badass." 的句号在引号里面
    const prevCore = (i) => { for (let k = i - 1; k >= 0; k--) if (!u[k].tag && !CLOSERS.has(u[k].s)) return u[k].s; return ''; };
    const nextVisible = (i) => { for (let k = i; k < u.length; k++) if (!u[k].tag) return { s: u[k].s, k }; return { s: '', k: u.length }; };
    const wordBefore = (i) => { let w = ''; for (let k = i - 1; k >= 0; k--) { if (u[k].tag) continue; if (!/[A-Za-z']/.test(u[k].s)) break; w = u[k].s + w; } return w; };
    const wordAfter = (i) => { let w = ''; for (let k = i; k < u.length; k++) { if (u[k].tag) continue; if (!/[A-Za-z']/.test(u[k].s)) break; w += u[k].s; } return w; };
    const res = []; // {pos, drop, q, speaker}
    for (let i = 1; i < u.length; i++) {
      const x = u[i];
      if (x.tag) continue;
      if (x.s === ' ') {
        const p = prevVisible(i), pc = prevCore(i);
        const n = nextVisible(i + 1);
        if (!p || !n.s) continue;
        const speaker = DASH_RE.test(n.s);
        let q = Q_SPACE;
        if (speaker) q = Q_SPEAKER;
        else if (SENT_END.has(pc)) q = Q_SENT;
        else if (CLAUSE.has(pc)) q = Q_CLAUSE;
        else if (isWide(p) && isWide(n.s)) q = Q_CJK_SPACE;
        else {
          const wb = wordBefore(i), wa = wordAfter(n.k);
          if (wb && BAD_END.has(wb.toLowerCase())) q = Q_BAD_END;
          else if (wb && wa && /^[A-Z]/.test(wb) && /^[A-Z]/.test(wa)) q = Q_NAME;
          else if (wa && GOOD_START.has(wa.toLowerCase())) q = Q_BEFORE_FUNC;
        }
        res.push({ pos: i, drop: true, q, speaker });
        continue;
      }
      const p = prevVisible(i);
      if (!p || p === ' ') continue;
      // 两个字符之间（没有空格）：至少一侧是全角字符才可以断
      if (!(isWide(p) || isWide(x.s))) continue;
      if (NO_LINE_START.has(x.s) || NO_LINE_END.has(p)) continue;
      // 断点放在紧挨着的标签之前（让 </i> 之类留在上一行末尾不太重要，这里保持简单：断在字符前）
      const pc = prevCore(i);
      let q = Q_CJK;
      if (SENT_END.has(pc)) q = Q_SENT;
      else if (CLAUSE.has(pc) || FW_PUNCT.has(p)) q = Q_CLAUSE;
      res.push({ pos: i, drop: false, q, speaker: false });
    }
    return res;
  }

  function wrap(text, limit) {
    text = String(text || '').trim();
    if (!text || !limit || textWidth(text) <= limit) return text;
    const u = units(text);
    const pre = [0];
    for (const x of u) pre.push(pre[pre.length - 1] + x.w);
    const cands = candidates(u);
    if (!cands.length) return text;
    // 断点序列：起点、候选、终点
    const P = [{ pos: 0, drop: false, q: 0 }].concat(cands, [{ pos: u.length, drop: false, q: 0, end: true }]);
    const width = (a, b) => {           // 从断点 a 之后到断点 b 之前的宽度
      const start = P[a].pos + (P[a].drop ? 1 : 0);
      return pre[P[b].pos] - pre[start];
    };
    const hasSpeakers = cands.some(c => c.speaker);
    const breakCost = (b) => {
      if (P[b].end) return 0;
      let c = P[b].q * 15;
      if (hasSpeakers && !P[b].speaker) c += 1000;     // 有多人对话时，尽量只在说话人之间断开
      return c;
    };
    const over = (w) => Math.max(0, w - limit);
    const n = P.length - 1;

    // 1) 最少行数（允许个别无法拆分的超长片段溢出，但溢出会被重罚）
    const minLines = new Array(n + 1).fill(Infinity), minOver = new Array(n + 1).fill(Infinity);
    minLines[0] = 0; minOver[0] = 0;
    for (let b = 1; b <= n; b++) {
      for (let a = 0; a < b; a++) {
        if (minLines[a] === Infinity) continue;
        const o = minOver[a] + over(width(a, b)), l = minLines[a] + 1;
        if (o < minOver[b] || (o === minOver[b] && l < minLines[b])) { minOver[b] = o; minLines[b] = l; }
      }
    }
    const L = minLines[n];
    if (L <= 1) return text;

    // 2) 固定行数 L，让各行宽度尽量接近平均值，并选择好的断点
    const target = pre[u.length] / L;
    const dp = Array.from({ length: L + 1 }, () => new Array(n + 1).fill(Infinity));
    const from = Array.from({ length: L + 1 }, () => new Array(n + 1).fill(-1));
    dp[0][0] = 0;
    for (let k = 1; k <= L; k++) {
      for (let b = 1; b <= n; b++) {
        for (let a = 0; a < b; a++) {
          if (dp[k - 1][a] === Infinity) continue;
          const w = width(a, b);
          const cost = dp[k - 1][a] + over(w) * 1e5 + BALANCE * (w - target) ** 2 + breakCost(b);
          if (cost < dp[k][b]) { dp[k][b] = cost; from[k][b] = a; }
        }
      }
    }
    const cuts = [];
    for (let k = L, b = n; k > 0; k--) { cuts.unshift(b); b = from[k][b]; }
    const lines = [];
    let prev = 0;
    for (const b of cuts) {
      const start = P[prev].pos + (P[prev].drop ? 1 : 0);
      lines.push(u.slice(start, P[b].pos).map(x => x.s).join('').trim());
      prev = b;
    }
    return lines.filter(Boolean).join('\n');
  }

  function reflow(text, limit) { return wrap(merge(text), limit); }

  // ---------------------------------------------------------------- 歌词 / 氛围音
  // 整条只有歌词（♪ … ♪、♫ … ♫、# … #）或音效描述（[音乐]、(birds chirping)、（笑声）、【掌声】）时返回 true。
  // 只有一部分是歌词的（例如「♪ la la ♪ - Stop singing!」）返回 false，照常翻译。
  const NOTES = '♪♫♬🎵🎶';
  const NOTE_SPAN = new RegExp(`[${NOTES}][^${NOTES}]*?(?:[${NOTES}]|(?=\\s[-–—－]\\s)|$)`, 'gu');
  const HASH_SPAN = /(?:^|(?<=\s))#\s[^#]*?(?:#|$)/g;          // 老式字幕用 # 标记歌词
  const SFX_SPAN = /\[[^\]]*\]|\([^)]*\)|（[^）]*）|【[^】]*】/g;
  const LEFTOVER = /[\s\-–—－.,!?…'"“”‘’:;，。！？、：；~～*]/g;
  function isLyricOrSfx(text) {
    let s = visible(text).replace(/\r?\n/g, ' ').trim();
    if (!s) return false;
    let found = false;
    for (const re of [NOTE_SPAN, HASH_SPAN, SFX_SPAN]) s = s.replace(re, () => { found = true; return ' '; });
    return found && s.replace(LEFTOVER, '') === '';
  }

  // ---------------------------------------------------------------- 双语字幕
  // 一条字幕里同时有中日韩文字和拉丁字母（英文等）时，拆成两种语言，各自合并成一行（超宽再换行），上下分行显示。
  const CJK_RE = /[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]/;
  const LATIN_RE = /[A-Za-zÀ-ɏЀ-ӿ]/;
  const OPENERS = /^[-–—－"“'‘(（\[【「『《]/;
  function tokClass(t) {
    const v = visible(t);
    if (CJK_RE.test(v)) return 'C';
    if (LATIN_RE.test(v)) return 'L';
    return 'N';
  }
  const latinWords = (toks) => toks.filter(t => tokClass(t) === 'L').length;
  const latinLetters = (toks) => toks.reduce((n, t) => n + (visible(t).match(/[A-Za-zÀ-ɏЀ-ӿ]/g) || []).length, 0);
  // 两段之间的中性符号（♪、引号、破折号）：一个时按方向归属，多个时平分
  function cutGap(toks, a, b) {
    const gap = b - a - 1;           // a：前一段最后一个文字，b：后一段第一个文字
    if (gap <= 0) return a + 1;
    if (gap === 1) return OPENERS.test(visible(toks[a + 1])) ? a + 1 : a + 2;
    return a + 1 + Math.ceil(gap / 2);
  }
  // 按行拆：有一行或几行是中文、其余是外文，且两种语言各自连在一起
  function splitByLines(text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n').map(x => x.trim()).filter(Boolean);
    if (lines.length < 2) return null;
    const cls = lines.map(l => CJK_RE.test(visible(l)) ? 'C' : LATIN_RE.test(visible(l)) ? 'L' : 'N');
    const real = cls.filter(c => c !== 'N');
    if (!real.includes('C') || !real.includes('L')) return null;
    const switches = real.filter((c, i) => i && c !== real[i - 1]).length;
    if (switches !== 1) return null;
    const first = real[0];
    let cut = cls.findIndex((c, i) => c !== 'N' && c !== first && i > 0);
    while (cut > 0 && cls[cut - 1] === 'N' && OPENERS.test(visible(lines[cut - 1]))) cut--;
    return [lines.slice(0, cut).join('\n'), lines.slice(cut).join('\n')];
  }
  // 按词拆：已经合并成一行的双语字幕（「要是大家知道 If people knew」）
  // loose=false 时外文部分至少 2 个词，避免把「我喜欢 iPhone」这种拆开
  function splitByWords(text, loose) {
    // 旧版本合并时会把「哦！」和「Oh!」直接连成「哦！Oh!」：在全角标点和紧跟的字母之间补一个空格再拆
    const toks = merge(text).replace(/([，。！？；：、…）」』》】])(?=[A-Za-zÀ-ɏЀ-ӿ])/g, '$1 ').split(' ').filter(Boolean);
    const cls = toks.map(tokClass);
    const minWords = loose ? 1 : 2;
    const lastC = cls.lastIndexOf('C');
    if (lastC >= 0 && lastC < toks.length - 1 && cls.slice(lastC + 1).every(c => c !== 'C')) {
      const firstL = cls.indexOf('L', lastC + 1);
      const rest = toks.slice(lastC + 1);
      if (firstL > 0 && latinWords(rest) >= minWords && latinLetters(rest) >= 2) {
        const cut = cutGap(toks, lastC, firstL);
        return [toks.slice(0, cut).join(' '), toks.slice(cut).join(' ')];
      }
    }
    const firstC = cls.indexOf('C');
    if (firstC > 0 && cls.slice(0, firstC).every(c => c !== 'C')) {
      const head = toks.slice(0, firstC);
      let lastL = -1; cls.forEach((c, i) => { if (i < firstC && c === 'L') lastL = i; });
      if (lastL >= 0 && latinWords(head) >= minWords && latinLetters(head) >= 2) {
        const cut = cutGap(toks, lastL, firstC);
        return [toks.slice(0, cut).join(' '), toks.slice(cut).join(' ')];
      }
    }
    return null;
  }
  // 一行里同时有两种语言（例如已经被合并过：「这人和他家人对我做了什么 what this man」）
  function hasMixedLine(text) {
    return String(text || '').split(/\r?\n/).some(l => splitByWords(l, true));
  }
  // mode：'lines' 原始双语文件，按行分开；'words' 两种语言已经被合并到同一行里，按词分开
  function splitBilingual(text, loose, mode) {
    if (mode === 'words') return splitByWords(text, loose);
    return splitByLines(text) || splitByWords(text, loose);
  }
  // 整个文件是否为双语字幕：至少 3 条、且三成以上的对白能明确拆成两种语言。
  // 返回 null（不是双语）、'lines' 或 'words'（见 splitBilingual）
  function detectBilingual(texts) {
    let total = 0, bi = 0, mixed = 0, clean = 0;
    for (const t of texts) {
      if (!t || !String(t).trim() || isLyricOrSfx(t)) continue;
      total++;
      const byLines = splitByLines(t);
      if (byLines || splitByWords(t, false)) bi++;
      if (hasMixedLine(t)) mixed++; else if (byLines) clean++;
    }
    if (bi < 3 || bi / Math.max(1, total) < 0.3) return null;
    return mixed > clean ? 'words' : 'lines';
  }
  // 双语文件里的一条：拆成两种语言各自排版；拆不开的（只有一种语言）按普通字幕排版
  function reflowBilingual(text, limit, mode) {
    const parts = splitBilingual(text, true, mode || 'lines');
    if (!parts) return reflow(text, limit);
    return parts.map(p => reflow(p, limit)).filter(Boolean).join('\n');
  }

  return { merge, wrap, reflow, textWidth, isWide, isLyricOrSfx, splitBilingual, detectBilingual, reflowBilingual };
});
