/* AI 字幕工作台 —— 前端逻辑（无框架、无构建） */
(() => {
  'use strict';

  // ---------- 常量 ----------
  const PRESETS = [
    { id: 'groq', name: 'Groq', base_url: 'https://api.groq.com/openai/v1', model: 'openai/gpt-oss-120b', key_url: 'https://console.groq.com/keys' },
    { id: 'siliconflow', name: '硅基流动 SiliconFlow', base_url: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-72B-Instruct', key_url: 'https://cloud.siliconflow.cn/account/ak' },
    { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-pro', key_url: 'https://platform.deepseek.com/api_keys' },
    { id: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', key_url: 'https://platform.openai.com/api-keys' },
    { id: 'openrouter', name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini', key_url: 'https://openrouter.ai/settings/keys' },
    { id: 'moonshot', name: 'Moonshot Kimi', base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k', key_url: 'https://platform.moonshot.cn/console/api-keys' },
    { id: 'zhipu', name: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash', key_url: 'https://open.bigmodel.cn/usercenter/apikeys' },
    { id: 'dashscope', name: '阿里云百炼 Qwen', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', key_url: 'https://bailian.console.aliyun.com/?tab=model#/api-key' },
    { id: 'ollama', name: 'Ollama 本地', base_url: 'http://localhost:11434/v1', model: 'qwen2.5:7b', key_url: '', key_tip: 'Ollama 本地运行无需 API Key，可留空。' },
    { id: 'server', name: '使用服务器预置配置', base_url: '', model: '', key_url: '', key_tip: '使用服务器 .env 中的 LLM_API_KEY，此处留空即可。' },
    { id: 'custom', name: '自定义', base_url: '', model: '', key_url: '' },
  ];
  // 旧版本默认模型已被服务商下线时自动替换
  const RETIRED_MODELS = { groq: ['llama-3.3-70b-versatile', 'llama-3.1-70b-versatile', 'mixtral-8x7b-32768'] };
  // 思考深度档位（具体哪些可选由服务器按服务商和模型决定，见 /api/thinking/options）
  const THINKING_LABELS = { off: '关闭', on: '开启', low: '轻度', medium: '中度', high: '重度', max: '最高', default: '不指定' };
  // 名称中含这些关键字的通常不是对话模型，列表里放到最后
  const NON_CHAT_RE = /whisper|tts|orpheus|guard|embed|rerank|bge-|flux|stable-diffusion|sdxl|kolors|speech|audio|transcri|moderation|dall-e|image|vision-only|ocr|cosyvoice|sensevoice/i;
  const LANGS = ['自动检测', '中文（简体）', '中文（繁體）', 'English', '日本語', '한국어', 'Français', 'Deutsch', 'Español',
    'Português', 'Русский', 'Italiano', 'العربية', 'ไทย', 'Tiếng Việt', 'Bahasa Indonesia', 'Türkçe', 'हिन्दी', 'Nederlands', 'Polski'];
  // 以下三项是旧版保存在浏览器里的设置，现在只用于一次性迁移到服务器
  const LS_SETTINGS = 'ais.settings.v1';
  const LS_PROFILES = 'ais.profiles.v1';
  const LS_TASK = 'ais.task.v1';
  const LS_PROJECT = 'ais.project.v1';
  const CONTEXT_N = 2; // 每批携带的上下文行数

  // ---------- 状态 ----------
  const state = {
    project: null,      // {filename, format, header, cues:[{id,start,end,text,translation,status,err,meta}]}
    profiles: [],       // 服务器上的模型配置（不含 Key）[{id,name,preset,base_url,model,...,has_key,key_hint}]
    activeId: '',       // 当前使用的配置 id
    profilesVersion: 0, // 服务器配置版本号，用于检测其他设备的修改
    taskVersion: 0,
    task: { mode: 'translate', source_lang: '自动检测', target_lang: '中文（简体）', tone: 'natural', glossary: '', instructions: '', batch_size: 20, concurrency: 3,
      line_merge: true, line_width: 50, font_size: 44, font_scale2: 75, skip_lyrics: true, export_res: 1080, ass_style: null, export_format: '', export_content: '' },
    running: false,
    abort: null,
    serverCfg: {},
  };

  const $ = (id) => document.getElementById(id);
  const rows = new Map(); // cue.id -> row element

  // ---------- 工具函数 ----------
  function toast(msg, kind = '') {
    const t = $('toast');
    t.textContent = msg; t.className = 'toast show ' + kind;
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2600);
  }
  function setStatus(el, msg, kind = '') { el.textContent = msg; el.className = 'status ' + kind; }
  function pad(n, w = 2) { return String(n).padStart(w, '0'); }
  function msToTime(ms) {
    ms = Math.max(0, Math.round(ms));
    const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60, f = ms % 1000;
    return `${pad(h)}:${pad(m)}:${pad(s)},${pad(f, 3)}`;
  }
  function timeToMs(str) {
    const m = String(str).trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?$/);
    if (!m) return null;
    const h = +(m[1] || 0), mi = +m[2], s = +m[3], f = +((m[4] || '0').padEnd(3, '0'));
    if (mi > 59 || s > 59) return null;
    return ((h * 60 + mi) * 60 + s) * 1000 + f;
  }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
  function autoGrow(ta) { ta.style.height = 'auto'; ta.style.height = (ta.scrollHeight + 2) + 'px'; }
  function authLost() {
    location.href = '/login.html?reason=expired&next=' + encodeURIComponent(location.pathname);
  }
  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (res.status === 401) { authLost(); throw new Error('登录已失效，请重新登录'); }
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { const j = await res.json(); msg = j.error || j.detail || msg; if (j.detail && typeof j.detail === 'object') msg += ' ' + JSON.stringify(j.detail).slice(0, 300); } catch (_) {}
      throw new Error(msg);
    }
    return res;
  }

  // ---------- 持久化 ----------
  // ---------- 字幕项目：保存在服务器，多台电脑自动同步 ----------
  // 每条字幕记住「上次与服务器一致时」的快照；保存时只上传有变化的条目，
  // 拉取时只下载其他设备改过的条目（服务器按版本号返回）。
  const sync = { version: 0, pid: '', snap: new Map(), saving: false, again: false, failed: false, needFull: false, retry: null };
  const SAVED_STATUS = ['done', 'error', 'edited'];
  function persistedStatus(c) { const st = (c.status === 'pending' || c.status === 'queued') ? (c._prev || '') : c.status; return SAVED_STATUS.includes(st) ? st : ''; }
  function cueSnap(c) { return JSON.stringify([c.start, c.end, c.text, c.translation || '', persistedStatus(c)]); }
  function cueForServer(c) {
    const o = { id: c.id, start: c.start, end: c.end, text: c.text, translation: c.translation || '', status: persistedStatus(c) };
    if (c.meta) o.meta = c.meta;
    if (c.raw !== undefined) o.raw = c.raw;
    return o;
  }
  function projectForServer(p) {
    return { project_id: p.project_id, filename: p.filename, format: p.format, header: p.header || '', cues: p.cues.map(cueForServer) };
  }
  function newPid() { return 'j' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }
  function resetSnap() { sync.snap = new Map(state.project ? state.project.cues.map(c => [c.id, cueSnap(c)]) : []); }
  function dirtyCues() { return state.project ? state.project.cues.filter(c => sync.snap.get(c.id) !== cueSnap(c)) : []; }
  function setSync(text, kind = '') { const el = $('syncState'); el.textContent = text; el.className = 'sync ' + kind; }
  function loadLocal() { /* 字幕项目改为保存在服务器，见 loadServerProject() */ }

  const flushSoon = debounce(() => flushProject(), 800);
  function saveProject() { if (state.project && !sync.failed) setSync('保存中…', 'busy'); flushSoon(); }
  async function putFullProject(p) {
    const res = await fetch('/api/project', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify({ project: projectForServer(p) }) });
    if (res.status === 401) { authLost(); throw new Error('登录已失效'); }
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || j.detail || `HTTP ${res.status}`);
    return j;
  }
  async function flushProject(keepalive = false) {
    if (!state.project) return;
    if (sync.saving) { sync.again = true; return; }
    clearTimeout(sync.retry);
    const dirty = sync.needFull ? state.project.cues : dirtyCues();
    if (!dirty.length) { setSync('已保存到服务器', 'ok'); return; }
    sync.saving = true; setSync('保存中…', 'busy');
    const snaps = dirty.map(c => [c.id, cueSnap(c)]);
    try {
      if (sync.needFull) {
        const j = await putFullProject(state.project);
        sync.pid = j.project_id; sync.version = j.version; sync.needFull = false;
      } else {
        const res = await fetch('/api/project', { method: 'PATCH', headers: jsonHeaders, keepalive,
          body: JSON.stringify({ project_id: sync.pid, cues: dirty.map(cueForServer) }) });
        if (res.status === 401) { authLost(); return; }
        if (res.status === 409) { sync.saving = false; await onProjectReplaced(dirty.length); return; }
        const j = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(j.error || j.detail || `HTTP ${res.status}`);
        const since = sync.version;
        sync.version = j.version;
        if (j.prev_version !== since) setTimeout(() => pullProject(since), 0); // 期间其他设备也改过，补拉一次
      }
      snaps.forEach(([id, sn]) => sync.snap.set(id, sn));
      sync.failed = false;
      setSync('已保存到服务器', 'ok');
    } catch (e) {
      sync.failed = true;
      setSync('⚠ 未保存到服务器，正在重试', 'err');
      sync.retry = setTimeout(() => flushProject(), 5000);
    } finally {
      sync.saving = false;
      if (sync.again) { sync.again = false; saveProject(); }
    }
  }
  function applyFullProject(p, version) {
    const { cues, ...rest } = p;
    state.project = { ...rest, cues: cues.map(c => { const { _v, ...x } = c; return { ...x, translation: x.translation || '', err: '' }; }) };
    sync.pid = p.project_id; sync.version = version; sync.needFull = false; sync.failed = false;
    resetSnap(); renderProject(); setSync('已保存到服务器', 'ok');
  }
  function refreshRowFull(c) {
    const row = rows.get(c.id); if (!row) return;
    const o = row.querySelector('.orig'), t = row.querySelector('.trans');
    if (o.value !== c.text) { o.value = c.text; autoGrow(o); }
    if (t.value !== (c.translation || '')) { t.value = c.translation || ''; autoGrow(t); }
    row.querySelector('.t-start').value = msToTime(c.start); row.querySelector('.t-end').value = msToTime(c.end);
    updateDur(row, c); applyRowStatus(row, c);
  }
  // 拉取其他设备的修改
  async function pullProject(since = sync.version) {
    if (sync.saving) return;
    let j;
    try { j = await (await api(`/api/project?since=${since}&pid=${encodeURIComponent(sync.pid)}`)).json(); } catch (_) { return; }
    if (!j.project && !j.changed) return;                 // 服务器上没有项目
    if (j.full) {
      if (j.project_id === sync.pid && j.version === sync.version) return;
      if (state.running) return;                          // 正在翻译时不切换文件
      const lost = dirtyCues().length;
      applyFullProject(j.project, j.version);
      toast(lost ? `其他设备打开了「${j.project.filename}」，已切换过来（本页 ${lost} 条未保存的修改无法合并）` : `已同步其他设备打开的「${j.project.filename}」`, lost ? 'err' : 'ok');
      return;
    }
    const dirtyIds = new Set(dirtyCues().map(c => c.id));
    const byId = new Map(state.project.cues.map(c => [c.id, c]));
    let applied = 0, deferred = false;
    for (const sc of j.changed) {
      const c = byId.get(sc.id); if (!c) continue;
      const incoming = JSON.stringify([sc.start, sc.end, sc.text, sc.translation || '', sc.status || '']);
      if (incoming === cueSnap(c)) { sync.snap.set(c.id, incoming); continue; }   // 内容相同（多半是自己刚保存的）
      if (dirtyIds.has(c.id)) continue;                                            // 本页有未保存的修改：本页优先
      const row = rows.get(c.id);
      if (c.status === 'pending' || c.status === 'queued' || (row && row.contains(document.activeElement))) { deferred = true; continue; }
      Object.assign(c, { start: sc.start, end: sc.end, text: sc.text, translation: sc.translation || '', status: sc.status || '', err: '' });
      sync.snap.set(c.id, incoming);
      refreshRowFull(c); applied++;
    }
    if (!deferred) sync.version = Math.max(sync.version, j.version);   // 有暂缓的条目时下次再拉
    if (applied) { updateCounter(); toast(`已同步其他设备修改的 ${applied} 条字幕`, 'ok'); }
  }
  async function onProjectReplaced(lost) {
    try {
      const j = await (await api('/api/project')).json();
      if (j.project) {
        applyFullProject(j.project, j.version);
        toast(`其他设备打开了另一个字幕文件「${j.project.filename}」，已切换过来（本页 ${lost} 条修改未能保存）`, 'err');
      }
    } catch (e) { setSync('⚠ 未保存到服务器', 'err'); }
  }
  function sameProject(a, b) {
    if (!a || !b || a.filename !== b.filename || a.cues.length !== b.cues.length) return false;
    return a.cues.every((c, i) => (c.translation || '') === (b.cues[i].translation || '') && c.text === b.cues[i].text);
  }
  // 启动时读取服务器上的字幕；旧版本保存在浏览器里的字幕会迁移上去
  async function loadServerProject() {
    const j = await (await api('/api/project')).json();
    const local = readLocalJSON(LS_PROJECT);
    if (local && Array.isArray(local.cues) && local.cues.length) {
      const done = local.cues.filter(c => (c.translation || '').trim()).length;
      let useLocal = !j.project;
      if (j.project && !sameProject(local, j.project)) {
        useLocal = confirm(`本浏览器里还有一份之前编辑的字幕「${local.filename || '未命名'}」（${local.cues.length} 条，已有译文 ${done} 条）。\n服务器上已有「${j.project.filename || '未命名'}」（${j.project.cues.length} 条）。\n\n确定：用本浏览器的这份替换服务器上的\n取消：保留服务器上的（本浏览器里的这份将被丢弃）`);
      }
      if (useLocal) {
        local.project_id = local.project_id || newPid();
        local.cues.forEach(c => { if (c.status === 'pending' || c.status === 'queued') c.status = c._prev || ''; delete c._prev; });
        const r = await putFullProject(local);
        localStorage.removeItem(LS_PROJECT);
        const fresh = await (await api('/api/project')).json();
        applyFullProject(fresh.project, fresh.version);
        toast(`已把本浏览器里的字幕「${local.filename || '未命名'}」迁移到服务器`, 'ok');
        return r;
      }
      localStorage.removeItem(LS_PROJECT);
    }
    if (j.project) applyFullProject(j.project, j.version);
    else { state.project = null; sync.pid = ''; sync.version = j.version || 0; renderProject(); }
  }
  // ---------- 模型配置（可保存多个） ----------
  const escHtml = (t) => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function newProfileId() { return 'p' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  function normalizeProfile(o = {}) {
    const preset = presetById(o.preset) ? o.preset : (o.base_url ? guessPreset(o.base_url) : 'groq');
    const pr = presetById(preset) || PRESETS[0];
    const p = {
      id: o.id || newProfileId(),
      name: (o.name || '').trim(),
      preset,
      base_url: o.base_url !== undefined ? o.base_url : pr.base_url,
      has_key: !!o.has_key,
      key_hint: o.key_hint || '',
      model: o.model !== undefined ? o.model : pr.model,
      temperature: Number.isFinite(+o.temperature) ? +o.temperature : 0.3,
      timeout: +o.timeout || 120,
      json_mode: !!o.json_mode,
      thinking: THINKING_LABELS[o.thinking] ? o.thinking : 'off',
    };
    // 服务商已下线的旧默认模型自动替换
    if ((RETIRED_MODELS[p.preset] || []).includes(p.model)) p.model = pr.model;
    return p;
  }
  function autoProfileName(p) {
    const pr = presetById(p.preset);
    if (p.preset === 'server') return '服务器预置' + (state.serverCfg.server_model ? ' · ' + state.serverCfg.server_model : '');
    const prov = pr && p.preset !== 'custom' ? pr.name.replace(/\s+[A-Za-z].*$/, '') || pr.name : ((p.base_url || '').replace(/^https?:\/\//, '').split('/')[0] || '自定义');
    const m = (p.model || '').split('/').pop();
    return m ? `${prov} · ${m}` : prov;
  }
  function profileLabel(p) { return (p && (p.name || autoProfileName(p))) || '未命名配置'; }
  function activeProfile() { return state.profiles.find(p => p.id === state.activeId) || state.profiles[0]; }
  const jsonHeaders = { 'Content-Type': 'application/json' };
  function readLocalJSON(key) { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) { return null; } }
  function applyServerProfiles(pr) {
    state.profiles = (pr.list || []).map(normalizeProfile);
    state.profilesVersion = pr.version || 0;
    state.activeId = state.profiles.some(p => p.id === pr.activeId) ? pr.activeId : (state.profiles[0] && state.profiles[0].id) || '';
    renderActiveSelect();
  }
  function applyServerTask(t) {
    state.taskVersion = t.version || 0;
    Object.assign(state.task, t.value || {});
    applyTaskToUI();
  }
  // 旧版本把配置（含 Key）存在浏览器里：这里取出来，准备上传到服务器
  function localLegacyProfiles() {
    const data = readLocalJSON(LS_PROFILES);
    if (data && Array.isArray(data.list) && data.list.length) return { activeId: data.activeId, list: data.list };
    const old = readLocalJSON(LS_SETTINGS);
    if (old && (old.api_key || old.base_url)) { const id = newProfileId(); return { activeId: id, list: [{ ...old, id }] }; }
    return null;
  }
  function profileForUpload(p, key) {
    const n = normalizeProfile(p);
    const out = { id: n.id, name: n.name, preset: n.preset, base_url: n.base_url, model: n.model,
      temperature: n.temperature, timeout: n.timeout, json_mode: n.json_mode, thinking: n.thinking };
    if (key !== undefined) out.api_key = key;
    return out;
  }
  async function putProfiles(body) {
    const res = await fetch('/api/settings/profiles', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify(body) });
    if (res.status === 401) { authLost(); throw new Error('登录已失效'); }
    const j = await res.json().catch(() => ({}));
    if (res.status === 409) { const e = new Error(j.error || '配置已被其他设备修改'); e.conflict = j.current; throw e; }
    if (!res.ok) throw new Error(j.error || j.detail || `HTTP ${res.status}`);
    return j;
  }
  // 启动时从服务器读取设置；首次使用时把本浏览器里的旧设置迁移上去
  async function loadServerSettings() {
    let data = await (await api('/api/settings')).json();
    const legacy = localLegacyProfiles();
    const serverIds = new Set(data.profiles.list.map(p => p.id));
    let toAdd = [];
    if (legacy) {
      // 服务器为空：全部迁移；服务器已有配置：只补充本浏览器独有、且填了 Key 的配置
      toAdd = legacy.list.filter(p => !serverIds.has(p.id) && (data.profiles.list.length === 0 || (p.api_key || '').trim()));
    }
    if (!data.profiles.list.length && !toAdd.length) {
      // 服务器上还没有任何配置：先给一个本地默认配置，等用户在「模型设置」里保存后才写入服务器，
      // 避免空白配置抢先占位，影响其他浏览器迁移已有的配置
      if (legacy) { localStorage.removeItem(LS_PROFILES); localStorage.removeItem(LS_SETTINGS); }
      const first = normalizeProfile({ preset: 'groq' });
      applyServerProfiles({ version: data.profiles.version, activeId: first.id, list: [first] });
      await loadServerTask(data);
      return;
    }
    if (toAdd.length) {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const list = data.profiles.list.map(p => profileForUpload(p)).concat(toAdd.map(p => profileForUpload(p, (p.api_key || '').trim())));
          // 服务器当前使用的配置没有 Key 时，改用迁移过来的配置
          const curActive = data.profiles.list.find(p => p.id === data.profiles.activeId);
          const curUsable = curActive && (curActive.has_key || ['ollama', 'server'].includes(curActive.preset));
          const legacyActive = legacy && toAdd.some(p => p.id === legacy.activeId) ? legacy.activeId : (toAdd[0] && toAdd[0].id);
          const activeId = curUsable ? data.profiles.activeId : (legacyActive || list[0].id);
          const saved = await putProfiles({ base_version: data.profiles.version, activeId, list });
          data = { ...data, profiles: saved.profiles };
          if (legacy) toast(`已把本浏览器的 ${toAdd.length} 个模型配置迁移到服务器`, 'ok');
          break;
        } catch (e) {
          if (!e.conflict || attempt) throw e;
          data = e.conflict;
        }
      }
    }
    if (legacy) { localStorage.removeItem(LS_PROFILES); localStorage.removeItem(LS_SETTINGS); } // 不在浏览器里留 Key
    applyServerProfiles(data.profiles);
    await loadServerTask(data);
  }
  async function loadServerTask(data) {
    const localTask = readLocalJSON(LS_TASK);
    if (!data.task.version && localTask) {
      Object.assign(state.task, localTask);
      try { applyServerTask(await (await api('/api/settings/task', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify({ task: state.task }) })).json()); } catch (_) {}
    } else {
      applyServerTask(data.task);
    }
    localStorage.removeItem(LS_TASK);
  }
  // 回到页面时同步其他设备的修改
  async function refreshServerSettings() {
    if (state.running || $('settingsDlg').open || taskSaveTimer) return;
    try {
      const data = await (await api('/api/settings')).json();
      if (data.profiles.version !== state.profilesVersion) applyServerProfiles(data.profiles);
      if (data.task.version !== state.taskVersion) applyServerTask(data.task);
    } catch (_) {}
  }
  function renderActiveSelect() {
    const sel = $('activeProfile');
    sel.innerHTML = '';
    for (const p of state.profiles) {
      const o = document.createElement('option');
      o.value = p.id; o.textContent = profileLabel(p);
      o.title = `${p.base_url || '(服务器预置)'}  ${p.model || ''}`;
      sel.appendChild(o);
    }
    sel.value = state.activeId;
    const a = activeProfile();
    sel.title = a ? `当前：${profileLabel(a)}\n${a.base_url || '(服务器预置)'}\n${a.model || ''}` : '';
  }
  async function setActiveProfile(id, silent = false) {
    if (!state.profiles.some(p => p.id === id)) return;
    const prev = state.activeId;
    state.activeId = id; renderActiveSelect();
    try {
      const pub = await (await api('/api/settings/active', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify({ activeId: id }) })).json();
      applyServerProfiles(pub.profiles);
      if (!silent) toast('当前模型：' + profileLabel(activeProfile()), 'ok');
    } catch (e) {
      state.activeId = prev; renderActiveSelect();
      toast('切换失败：' + e.message, 'err');
    }
  }
  // 任务设置：输入停止 0.6 秒后保存到服务器
  let taskSaveTimer = null;
  function saveTask() {
    clearTimeout(taskSaveTimer);
    taskSaveTimer = setTimeout(async () => {
      taskSaveTimer = null;
      try {
        const t = await (await api('/api/settings/task', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify({ task: state.task }) })).json();
        state.taskVersion = t.version;
      } catch (e) { toast('任务设置保存失败：' + e.message, 'err'); }
    }, 600);
  }
  // 关闭或刷新页面时，把还没发出的任务设置发出去
  function flushTask() {
    if (!taskSaveTimer) return;
    clearTimeout(taskSaveTimer); taskSaveTimer = null;
    fetch('/api/settings/task', { method: 'PUT', headers: jsonHeaders, keepalive: true, body: JSON.stringify({ task: state.task }) }).catch(() => {});
  }

  // ---------- 设置面板 ----------
  function presetById(id) { return PRESETS.find(x => x.id === id); }
  function guessPreset(baseUrl) {
    const u = (baseUrl || '').trim().replace(/\/+$/, '');
    const p = PRESETS.find(x => x.base_url && x.base_url === u);
    return p ? p.id : 'custom';
  }
  function updateKeyLink() {
    const p = presetById($('preset').value);
    const link = $('keyLink');
    if (p && p.key_url) { link.href = p.key_url; link.hidden = false; link.title = `打开 ${p.name} 官网的 API Key 页面`; }
    else { link.hidden = true; link.removeAttribute('href'); }
    $('keyTip').textContent = (p && p.key_tip) || '';
  }

  // ----- 模型下拉框（替代原生 datalist：datalist 会按输入框已有内容过滤，导致下拉为空） -----
  const modelCombo = { models: [], open: false, active: -1, filter: '' };
  function setModelOptions(list) { modelCombo.models = list.slice(); renderModelMenu(); }
  function renderModelMenu() {
    const menu = $('modelMenu'), cur = $('model').value.trim();
    const q = modelCombo.filter.trim().toLowerCase();
    if (!modelCombo.models.length) {
      menu.innerHTML = '<div class="combo-empty">暂无列表，请先填写 API 地址和 Key，再点「获取列表」</div>';
      return;
    }
    const match = modelCombo.models.filter(m => !q || m.toLowerCase().includes(q));
    const chat = match.filter(m => !NON_CHAT_RE.test(m)), other = match.filter(m => NON_CHAT_RE.test(m));
    const esc = (t) => t.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const item = (m, dim) => `<div class="combo-item${dim ? ' dim' : ''}${m === cur ? ' current' : ''}" role="option" data-v="${esc(m)}" title="${esc(m)}">${esc(m)}${m === cur ? '  ✓' : ''}</div>`;
    let html = chat.map(m => item(m, false)).join('');
    if (other.length) html += '<div class="combo-group">其他（可能不是对话模型）</div>' + other.map(m => item(m, true)).join('');
    menu.innerHTML = html || `<div class="combo-empty">没有匹配「${esc(modelCombo.filter)}」的模型</div>`;
    modelCombo.active = -1;
  }
  function placeModelMenu() {
    const menu = $('modelMenu'), r = $('model').getBoundingClientRect();
    const gap = 4, maxH = 280;
    const below = window.innerHeight - r.bottom - gap - 8, above = r.top - gap - 8;
    const up = below < Math.min(maxH, menu.scrollHeight) && above > below;
    const h = Math.max(120, Math.min(maxH, up ? above : below));
    menu.style.left = r.left + 'px';
    menu.style.width = ($('modelCombo').getBoundingClientRect().width) + 'px';
    menu.style.maxHeight = h + 'px';
    if (up) { menu.style.top = ''; menu.style.bottom = (window.innerHeight - r.top + gap) + 'px'; }
    else { menu.style.bottom = ''; menu.style.top = (r.bottom + gap) + 'px'; }
  }
  function openModelMenu(filter = '') {
    modelCombo.filter = filter; renderModelMenu();
    $('modelMenu').hidden = false; modelCombo.open = true; placeModelMenu();
    const cur = $('modelMenu').querySelector('.combo-item.current');
    if (cur) cur.scrollIntoView({ block: 'nearest' });
  }
  function closeModelMenu() { $('modelMenu').hidden = true; modelCombo.open = false; }
  function pickModel(v) { $('model').value = v; closeModelMenu(); commitForm(); setStatus($('settingsMsg'), `已选择模型 ${v}，可点「测试连接」验证`, 'ok'); }
  // ----- 字段注解：点击 ⓘ 弹出说明 -----
  let openTip = null; // {btn, pop}
  function closeInfoTip() {
    if (!openTip) return;
    openTip.pop.hidden = true; openTip.btn.setAttribute('aria-expanded', 'false'); openTip = null;
  }
  function placeInfoTip(btn, pop) {
    const r = btn.getBoundingClientRect(), m = 12;
    const w = pop.offsetWidth, h = pop.offsetHeight;
    let left = Math.min(Math.max(m, r.left - 12), window.innerWidth - w - m);
    let top = r.bottom + 8;
    if (top + h > window.innerHeight - m) top = Math.max(m, r.top - h - 8); // 下方放不下时显示在上方
    pop.style.left = left + 'px'; pop.style.top = top + 'px';
  }
  function updateTipNotes() {
    const note = $('tempTipNote'); if (!note) return;
    note.textContent = (thinkingSpec && thinkingSpec.temp_ignored.includes($('thinking').value))
      ? '当前配置开启了深度思考。DeepSeek 在思考模式下会忽略温度，改这里不会有效果。'
      : '';
  }
  function initInfoTips() {
    document.querySelectorAll('.info-btn[data-tip]').forEach(btn => {
      const pop = $(btn.dataset.tip);
      btn.addEventListener('click', (e) => {
        e.preventDefault(); e.stopPropagation();
        const same = openTip && openTip.btn === btn;
        closeInfoTip();
        if (same) return;
        updateTipNotes();
        pop.hidden = false; btn.setAttribute('aria-expanded', 'true'); openTip = { btn, pop };
        placeInfoTip(btn, pop);
      });
      pop.querySelector('.info-close').addEventListener('click', () => { closeInfoTip(); btn.focus(); });
    });
    // 点击说明框以外的地方关闭
    document.addEventListener('mousedown', (e) => {
      if (openTip && !openTip.pop.contains(e.target) && e.target !== openTip.btn) closeInfoTip();
    });
    window.addEventListener('resize', () => { if (openTip) placeInfoTip(openTip.btn, openTip.pop); });
    $('settingsDlg').addEventListener('scroll', () => { if (openTip) placeInfoTip(openTip.btn, openTip.pop); }, true);
    // Esc 先关闭说明框，不关闭整个对话框
    $('settingsDlg').addEventListener('cancel', (e) => { if (openTip) { e.preventDefault(); const b = openTip.btn; closeInfoTip(); b.focus(); } });
    $('settingsDlg').addEventListener('close', closeInfoTip);
  }
  function initModelCombo() {
    const input = $('model'), menu = $('modelMenu');
    $('modelToggle').addEventListener('mousedown', (e) => { e.preventDefault(); });
    $('modelToggle').addEventListener('click', () => { if (modelCombo.open) closeModelMenu(); else { openModelMenu(''); input.focus(); } });
    input.addEventListener('focus', () => { if (modelCombo.models.length) openModelMenu(''); });
    input.addEventListener('input', () => openModelMenu(input.value));
    input.addEventListener('blur', () => setTimeout(closeModelMenu, 150));
    input.addEventListener('keydown', (e) => {
      const items = [...menu.querySelectorAll('.combo-item')];
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!modelCombo.open) { openModelMenu(''); return; }
        if (!items.length) return;
        modelCombo.active = (modelCombo.active + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items.forEach((el, i) => el.classList.toggle('active', i === modelCombo.active));
        items[modelCombo.active].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter' && modelCombo.open && modelCombo.active >= 0 && items[modelCombo.active]) {
        e.preventDefault(); pickModel(items[modelCombo.active].dataset.v);
      } else if (e.key === 'Escape' && modelCombo.open) {
        e.preventDefault(); e.stopPropagation(); closeModelMenu();
      }
    });
    menu.addEventListener('mousedown', (e) => {
      const it = e.target.closest('.combo-item'); if (!it) return;
      e.preventDefault(); pickModel(it.dataset.v);
    });
    // 窗口尺寸变化或对话框滚动时跟随输入框
    window.addEventListener('resize', () => { if (modelCombo.open) placeModelMenu(); });
    $('settingsDlg').addEventListener('scroll', () => { if (modelCombo.open) placeModelMenu(); }, true);
    // Esc 只关闭下拉，不关闭整个对话框
    $('settingsDlg').addEventListener('cancel', (e) => { if (modelCombo.open) { e.preventDefault(); closeModelMenu(); } });
  }
  async function fetchModels(silent = false) {
    if (!silent) setStatus($('settingsMsg'), '正在获取模型列表…');
    const btn = $('fetchModelsBtn'); btn.disabled = true;
    try {
      commitForm(false);
      const dp = draftProvider(draftEditing());
      const r = await api('/api/models', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ base_url: dp.base_url, api_key: dp.api_key, profile_id: dp.profile_id }) });
      const { models } = await r.json();
      modelCache.set(modelCacheKey(draftEditing()), models);
      setModelOptions(models);
      const cur = $('model').value.trim();
      const chat = models.filter(m => !NON_CHAT_RE.test(m));
      let msg = `获取到 ${models.length} 个模型，点模型框右侧 ▾ 选择`;
      if (cur && !models.includes(cur)) msg = `当前模型「${cur}」不在可用列表中（可能已下线），请从下拉中重新选择`;
      setStatus($('settingsMsg'), msg, cur && !models.includes(cur) ? 'err' : 'ok');
      if (!silent && (chat.length || models.length)) { $('model').focus(); openModelMenu(''); }
      return models;
    } catch (e) { if (!silent) setStatus($('settingsMsg'), '获取失败：' + e.message, 'err'); return null; }
    finally { btn.disabled = false; }
  }
  function explainError(msg) {
    if (/model_not_found|does not exist|not found|404/i.test(msg) && /model/i.test(msg)) return msg + '\n提示：该模型不存在、已下线或当前 Key 无权使用。请点「获取列表」，从下拉中选择一个可用模型。';
    if (/401|invalid.?api.?key|unauthorized|authentication/i.test(msg)) return msg + '\n提示：API Key 无效，请检查是否复制完整，或点「获取 Key ↗」重新创建。';
    if (/429|rate.?limit/i.test(msg)) return msg + '\n提示：触发了服务商限流，请稍后再试、换一个模型，或调小每批条数和并发数。';
    if (/无法解析/.test(msg)) return msg + '\n提示：模型偶尔会输出格式不完整的结果，可点「重试失败项」重新处理这几条。';
    return msg;
  }

  function initSettingsUI() {
    const sel = $('preset');
    sel.innerHTML = PRESETS.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
    sel.addEventListener('change', () => {
      const p = presetById(sel.value);
      if (p && p.id !== 'custom' && p.id !== 'server') { $('baseUrl').value = p.base_url; $('model').value = p.model; }
      if (p && p.id === 'server') { $('baseUrl').value = ''; $('model').value = ''; $('apiKey').value = ''; }
      setModelOptions([]); updateKeyLink(); setStatus($('settingsMsg'), '');
    });
    $('baseUrl').addEventListener('change', () => { setModelOptions([]); });
    $('clearKeyBtn').addEventListener('click', () => {
      const d = draftEditing(); if (!d) return;
      d.clear_key = true; d.new_key = ''; $('apiKey').value = ''; dlg.changed.add(d.id); updateKeyField(d); renderProfileList();
    });
    $('thinking').addEventListener('change', () => {
      const d = draftEditing(); if (d) d._want = $('thinking').value;
      updateTipNotes();
    });
    $('toggleKeyBtn').addEventListener('click', () => {
      const k = $('apiKey'); const show = k.type === 'password';
      k.type = show ? 'text' : 'password'; $('toggleKeyBtn').textContent = show ? '隐藏' : '显示';
    });
    initInfoTips();
    initModelCombo();
    $('fetchModelsBtn').addEventListener('click', () => fetchModels(false));
    $('testBtn').addEventListener('click', async () => {
      setStatus($('settingsMsg'), '正在测试…');
      $('testBtn').disabled = true;
      try {
        const r = await api('/api/translate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: draftProvider(draftEditing()), task: { mode: 'translate', source_lang: 'English', target_lang: '中文（简体）' }, items: [{ id: 1, text: 'Hello, world!' }] }) });
        const j = await r.json();
        const th = j.thinking ? `（思考：${THINKING_LABELS[j.thinking] || j.thinking}）` : '';
        setStatus($('settingsMsg'), `连接成功 ✓ 模型 ${j.model}${th} 返回：「${j.results[0].text}」` +
          (j.thinking_ignored ? '\n注意：服务商不接受所选的思考深度参数，已按「不指定」执行。' : ''), j.thinking_ignored ? 'err' : 'ok');
      } catch (e) { setStatus($('settingsMsg'), '测试失败：' + explainError(e.message), 'err'); }
      finally { $('testBtn').disabled = false; }
    });
    $('settingsSave').addEventListener('click', () => commitDialog(false));
    $('settingsSaveUse').addEventListener('click', () => commitDialog(true));
    $('settingsCancel').addEventListener('click', () => { if (confirmDiscard()) $('settingsDlg').close(); });
    $('settingsDlg').addEventListener('cancel', (e) => { if (!e.defaultPrevented && !confirmDiscard()) e.preventDefault(); });
    $('settingsBtn').addEventListener('click', openSettings);
    $('activeProfile').addEventListener('change', (e) => setActiveProfile(e.target.value));

    // 表单任意字段变化 → 写回正在编辑的配置（草稿）
    const form = document.querySelector('#settingsDlg .profile-form');
    const onEdit = (e) => { if (e.target.id === 'jsonMode' || e.target.closest('.profile-form')) commitForm(); };
    form.addEventListener('input', onEdit); form.addEventListener('change', onEdit);
    $('profileList').addEventListener('click', (e) => {
      const it = e.target.closest('.profile-item'); if (!it || it.dataset.id === dlg.editingId) return;
      commitForm(false); dlg.editingId = it.dataset.id; loadForm(draftEditing()); renderProfileList(); setStatus($('settingsMsg'), '');
    });
    $('newProfileBtn').addEventListener('click', () => {
      commitForm(false);
      const p = normalizeProfile({ preset: 'groq' });
      dlg.list.push(p); dlg.changed.add(p.id); dlg.editingId = p.id;
      loadForm(p); renderProfileList();
      setStatus($('settingsMsg'), '已新建配置：选择服务商、填写 Key 和模型后点「保存」', 'ok');
      $('apiKey').focus();
    });
    $('dupProfileBtn').addEventListener('click', () => {
      commitForm(false);
      const src = draftEditing(); if (!src) return;
      const p = normalizeProfile({ ...src, id: '', name: profileLabel(src) + ' 副本', has_key: false });
      // 复制时沿用原配置的 Key：新填的直接带上；已保存的由服务器复制
      if (src.new_key) p.new_key = src.new_key;
      else if (!keyDropped(src) && (src.has_key || src.copy_key_from)) { p.copy_key_from = src.has_key ? src.id : src.copy_key_from; p.copy_key_hint = src.key_hint || src.copy_key_hint; p.saved_base_url = src.saved_base_url; }
      dlg.list.splice(dlg.list.indexOf(src) + 1, 0, p); dlg.changed.add(p.id); dlg.editingId = p.id;
      loadForm(p); renderProfileList();
      setStatus($('settingsMsg'), '已复制，可修改后保存（例如换一个模型）', 'ok');
    });
    $('deleteProfileBtn').addEventListener('click', () => {
      const p = draftEditing(); if (!p) return;
      if (dlg.list.length <= 1) { setStatus($('settingsMsg'), '至少需要保留一个配置', 'err'); return; }
      if (!confirm(`删除配置「${profileLabel(p)}」？（点「保存」后生效）`)) return;
      const i = dlg.list.indexOf(p); dlg.list.splice(i, 1); dlg.deleted = true;
      if (dlg.activeId === p.id) dlg.activeId = dlg.list[0].id;
      dlg.editingId = dlg.list[Math.min(i, dlg.list.length - 1)].id;
      loadForm(draftEditing()); renderProfileList();
      setStatus($('settingsMsg'), '已删除，点「保存」后生效', 'ok');
    });
  }
  function readSettingsForm() {
    return {
      base_url: $('baseUrl').value.trim(), model: $('model').value.trim(),
      temperature: parseFloat($('temperature').value) || 0, timeout: parseInt($('timeout').value) || 120, json_mode: $('jsonMode').value === '1',
      thinking: $('thinking').value,
    };
  }
  // 对话框中的草稿：所有修改在点「保存」前都不会影响正在使用的配置
  const dlg = { list: [], activeId: '', editingId: '', changed: new Set(), deleted: false };
  const modelCache = new Map(); // `${base_url}|${Key 来源}` -> 模型列表
  const normUrl = (u) => (u || '').trim().replace(/\/+$/, '');
  // 已保存的 Key 只能用于原来的 API 地址：地址改了或点了「清除」，就当作没有 Key
  function keyDropped(p) { return !!p.clear_key || ((p.has_key || !!p.copy_key_from) && normUrl(p.base_url) !== normUrl(p.saved_base_url)); }
  function keyRef(p) { return p.new_key || keyDropped(p) ? '' : (p.has_key ? p.id : (p.copy_key_from || '')); }
  function modelCacheKey(p) { return `${(p.base_url || '').trim()}|${p.new_key || keyRef(p)}`; }
  // 对话框里「测试连接」「获取列表」用的参数：新填的 Key 直接发送，否则由服务器用已保存的 Key
  function draftProvider(p) {
    return { base_url: p.base_url, model: p.model, temperature: p.temperature, timeout: p.timeout, json_mode: p.json_mode,
      thinking: p.thinking, api_key: p.new_key || '', profile_id: keyRef(p) };
  }
  function updateKeyField(p) {
    const input = $('apiKey');
    let ph, state = '';
    const hadKey = p.has_key || !!p.copy_key_from;
    if (p.new_key) state = hadKey ? '保存后替换服务器上的 Key' : '';
    if (!p.new_key && keyDropped(p)) {
      ph = '请输入 API Key';
      state = p.clear_key ? '保存后将清除原来的 Key' : 'API 地址已修改，原来的 Key 不再沿用，请重新填写';
    }
    else if (p.has_key) ph = `已保存在服务器：${p.key_hint}（留空保持不变）`;
    else if (p.copy_key_from) ph = `沿用原配置的 Key：${p.copy_key_hint || ''}（留空保持不变）`;
    else ph = 'sk-…（保存在服务器，不会再显示在浏览器）';
    input.placeholder = ph;
    $('keyState').textContent = state;
    $('clearKeyBtn').hidden = !(hadKey && !keyDropped(p));
  }
  function draftEditing() { return dlg.list.find(p => p.id === dlg.editingId); }
  function isDirty() { return dlg.changed.size > 0 || dlg.deleted || dlg.activeId !== state.activeId; }
  function confirmDiscard() { return !isDirty() || confirm('有未保存的修改，确定放弃？'); }
  function loadForm(p) {
    if (!p) return;
    $('profileName').value = p.name || '';
    $('profileName').placeholder = autoProfileName(p);
    $('preset').value = presetById(p.preset) ? p.preset : guessPreset(p.base_url);
    updateKeyLink(); closeModelMenu();
    $('apiKey').type = 'password'; $('toggleKeyBtn').textContent = '显示';
    $('baseUrl').value = p.base_url || ''; $('apiKey').value = p.new_key || ''; $('model').value = p.model || '';
    updateKeyField(p);
    $('temperature').value = p.temperature; $('timeout').value = p.timeout; $('jsonMode').value = p.json_mode ? '1' : '0';
    if (!p._want) p._want = p.thinking || 'off';   // 用户想要的档位；换模型时按它重新换算
    setThinkingPlaceholder(p.thinking || 'off');
    thinkingKey = ''; refreshThinking(p, true);
    setModelOptions(modelCache.get(modelCacheKey(p)) || []);
    $('deleteProfileBtn').disabled = dlg.list.length <= 1;
    $('settingsSaveUse').textContent = p.id === dlg.activeId ? '保存' : '保存并使用';
    $('settingsSave').hidden = p.id === dlg.activeId;
  }
  // ---------- 思考深度 ----------
  let thinkingSpec = null, thinkingKey = '', thinkingReq = 0, thinkingTimer = null;
  function setThinkingPlaceholder(v) {
    $('thinking').innerHTML = `<option value="${v}">${THINKING_LABELS[v] || v}</option>`;
    $('thinking').value = v;
  }
  function refreshThinking(p, now = false) {
    thinkingKey = `${p.base_url}|${p.model}`;
    clearTimeout(thinkingTimer);
    thinkingTimer = setTimeout(() => loadThinking(p), now ? 0 : 350);
  }
  async function loadThinking(p) {
    const req = ++thinkingReq;
    let spec;
    try {
      spec = await (await api('/api/thinking/options', { method: 'POST', headers: jsonHeaders,
        body: JSON.stringify({ base_url: p.base_url, model: p.model, current: p._want || p.thinking || 'off' }) })).json();
    } catch (e) { $('thinkingNote').textContent = '读取思考深度档位失败：' + e.message; return; }
    if (req !== thinkingReq || draftEditing() !== p) return;     // 已切换到别的模型或配置
    thinkingSpec = spec;
    const sel = $('thinking');
    sel.innerHTML = spec.levels.map(l => `<option value="${l.id}">${escHtml(l.label)}</option>`).join('');
    sel.disabled = !spec.adjustable;
    sel.value = spec.resolved;
    // 保存的档位该模型不支持时（例如 gpt-oss 不能关闭），自动换成最接近的一档
    if (p.thinking !== spec.resolved) { p.thinking = spec.resolved; renderProfileList(); }
    $('thinkingNote').textContent = (spec.provider ? spec.provider + '：' : '') + spec.note;
    updateThinkingWarn(); updateTipNotes();
  }
  function updateThinkingWarn() {
    const el = $('thinkingWarn'); if (!el) return;
    const v = $('thinking').value, t = parseInt($('timeout').value) || 120;
    el.textContent = (thinkingSpec && thinkingSpec.slow.includes(v) && t < 300)
      ? `这一档思考较深，单批可能要几分钟。建议把超时调到 300 秒以上（现在是 ${t} 秒），或调小每批条数。` : '';
  }
  function commitForm(mark = true) {
    const p = draftEditing(); if (!p) return;
    const before = JSON.stringify(p);
    Object.assign(p, readSettingsForm(), { name: $('profileName').value.trim(), preset: $('preset').value });
    const typed = $('apiKey').value.trim();
    if (typed) { p.new_key = typed; p.clear_key = false; } else delete p.new_key;
    updateKeyField(p);
    if (`${p.base_url}|${p.model}` !== thinkingKey) refreshThinking(p);
    updateThinkingWarn();
    if (mark && JSON.stringify(p) !== before) dlg.changed.add(p.id);
    $('profileName').placeholder = autoProfileName(p);
    renderProfileList();
  }
  function renderProfileList() {
    $('profileList').innerHTML = dlg.list.map(p => `
      <div class="profile-item${p.id === dlg.editingId ? ' editing' : ''}" data-id="${p.id}" role="option" title="${escHtml((p.base_url || '(服务器预置)') + '\n' + (p.model || ''))}">
        <div class="p-name"><span>${escHtml(profileLabel(p))}</span>${p.id === dlg.activeId ? '<span class="badge">使用中</span>' : ''}${dlg.changed.has(p.id) ? '<span class="badge dirty">未保存</span>' : ''}</div>
        <div class="p-model">${escHtml(p.model || '（未填写模型）')}${p.thinking && p.thinking !== 'off' ? ' · 思考：' + (THINKING_LABELS[p.thinking] || p.thinking) : ''}</div>
      </div>`).join('');
  }
  async function commitDialog(use) {
    commitForm();
    const p = draftEditing();
    if (p && p.preset !== 'server' && !p.base_url) { setStatus($('settingsMsg'), '请填写 API 地址', 'err'); return; }
    if (p && p.preset !== 'server' && !p.model) { setStatus($('settingsMsg'), '请填写或选择模型', 'err'); return; }
    const activeId = use && p ? p.id : dlg.activeId;
    const list = dlg.list.map(x => {
      const out = profileForUpload(x);
      if (x.new_key) out.api_key = x.new_key;
      else if (keyDropped(x)) out.api_key = '';
      else if (x.copy_key_from) out.copy_key_from = x.copy_key_from;
      return out;
    });
    const btns = ['settingsSave', 'settingsSaveUse'].map($); btns.forEach(b => b.disabled = true);
    setStatus($('settingsMsg'), '正在保存到服务器…');
    try {
      const pub = await putProfiles({ base_version: state.profilesVersion, activeId, list });
      applyServerProfiles(pub.profiles);
      dlg.changed.clear(); dlg.deleted = false;
      $('settingsDlg').close();
      toast(`已保存 ${state.profiles.length} 个配置 · 当前使用：${profileLabel(activeProfile())}`, 'ok');
    } catch (e) {
      if (e.conflict) {
        applyServerProfiles(e.conflict.profiles);
        dlg.changed.clear(); dlg.deleted = false;
        openSettings();
        setStatus($('settingsMsg'), '其他设备刚刚修改了模型设置，已加载最新内容。请重新修改后再保存。', 'err');
      } else setStatus($('settingsMsg'), '保存失败：' + e.message, 'err');
    } finally { btns.forEach(b => b.disabled = false); }
  }
  function openSettings() {
    dlg.list = state.profiles.map(p => ({ ...p, saved_base_url: p.base_url }));
    dlg.activeId = state.activeId; dlg.editingId = state.activeId;
    dlg.changed = new Set(); dlg.deleted = false;
    const c = state.serverCfg;
    $('serverKeyHint').textContent = c.server_has_key ? `（服务器已预置密钥${c.server_model ? '，模型 ' + c.server_model : ''}，留空即可使用）` : '';
    loadForm(draftEditing()); renderProfileList();
    setStatus($('settingsMsg'), '');
    if (!$('settingsDlg').open) $('settingsDlg').showModal();
  }

  // ---------- 任务面板 ----------
  function initTaskUI() {
    $('langList').innerHTML = LANGS.map(l => `<option value="${l}">`).join('');
    applyTaskToUI();
    $('modeSeg').addEventListener('click', (e) => {
      const v = e.target.closest('button') && e.target.closest('button').dataset.v; if (!v) return;
      if (state.running) { toast('正在处理中，停止后再切换模式'); return; }
      if (v === 'none') { if (state.task.mode !== 'none') { setMode('none'); saveTask(); } return; }
      let { t, p } = modeFlags(state.task.mode);
      if (v === 'translate') t = !t; else p = !p;
      // 翻译、润色都取消 = 仅编辑（不使用 AI）
      setMode(t && p ? 'translate_polish' : p ? 'polish' : t ? 'translate' : 'none'); saveTask();
    });
    const bind = (id, key, parse = (v) => v) => $(id).addEventListener('input', () => { state.task[key] = parse($(id).value); saveTask(); });
    bind('srcLang', 'source_lang'); bind('tgtLang', 'target_lang'); bind('tone', 'tone');
    bind('glossary', 'glossary'); bind('instructions', 'instructions');
    bind('batchSize', 'batch_size', (v) => Math.min(100, Math.max(1, parseInt(v) || 20)));
    bind('concurrency', 'concurrency', (v) => Math.min(10, Math.max(1, parseInt(v) || 3)));
    $('lineMerge').addEventListener('change', () => { state.task.line_merge = $('lineMerge').checked; updateLineWidthHint(); saveTask(); });
    $('skipLyrics').addEventListener('change', () => {
      state.task.skip_lyrics = $('skipLyrics').checked; saveTask();
      if (state.task.skip_lyrics && state.project) {
        // 之前已经翻译过的歌词 / 氛围音：询问是否清空，清空后导出时显示原文
        const had = state.project.cues.filter(c => isSkip(c) && c.translation.trim());
        if (had.length && confirm(`有 ${had.length} 条歌词或氛围音已经有译文。要清空这些译文吗？\n清空后导出时这些行显示原文。`)) {
          for (const c of had) { c.translation = ''; c.status = ''; c.err = ''; refreshRow(c); }
          saveProject();
          toast(`已清空 ${had.length} 条歌词或氛围音的译文`, 'ok');
        }
      }
      refreshAllRowStatus();
    });
    $('lineWidth').addEventListener('input', () => {
      const v = parseInt($('lineWidth').value);
      if (v >= 16 && v <= 120) { state.task.line_width = v; updateLineWidthHint(); saveTask(); }
    });

    $('runAllBtn').addEventListener('click', () => runTranslate(state.project.cues));
    $('runEmptyBtn').addEventListener('click', () => runTranslate(state.project.cues.filter(c => !c.translation.trim())));
    $('runSelBtn').addEventListener('click', () => runTranslate(selectedCues()));
    $('retryErrBtn').addEventListener('click', () => runTranslate(state.project.cues.filter(c => c.status === 'error' && !isSkip(c))));
    $('stopBtn').addEventListener('click', () => { if (state.abort) state.abort.abort(); });
  }
  function applyTaskToUI() {
    const t = state.task;
    $('srcLang').value = t.source_lang; $('tgtLang').value = t.target_lang; $('tone').value = t.tone;
    $('glossary').value = t.glossary; $('instructions').value = t.instructions;
    $('batchSize').value = t.batch_size; $('concurrency').value = t.concurrency;
    $('lineMerge').checked = t.line_merge !== false; $('lineWidth').value = lineWidth();
    $('skipLyrics').checked = t.skip_lyrics !== false;
    refreshAllRowStatus();
    updateLineWidthHint();
    setMode(t.mode);
  }
  // mode: translate | polish | translate_polish（翻译后再润色）
  // mode：translate | polish | translate_polish | none（仅编辑，不使用 AI）
  function modeFlags(mode) {
    if (mode === 'none') return { t: false, p: false };
    return { t: mode !== 'polish', p: mode === 'polish' || mode === 'translate_polish' };
  }
  const AI_FIELDS = ['tone', 'glossary', 'instructions', 'batchSize', 'concurrency'];
  function isEditOnly() { return state.task.mode === 'none'; }
  // 仅编辑时禁用所有 AI 运行按钮（renderProject / setRunningUI / updateCounter 之后都会调用）
  function applyModeLock() {
    if (!isEditOnly()) return;
    ['runAllBtn', 'runEmptyBtn', 'runSelBtn', 'retryErrBtn'].forEach(id => $(id).disabled = true);
  }
  function setMode(mode) {
    if (!['translate', 'polish', 'translate_polish', 'none'].includes(mode)) mode = 'translate';
    state.task.mode = mode;
    const none = mode === 'none';
    const { t, p } = modeFlags(mode);
    for (const b of $('modeSeg').querySelectorAll('button')) {
      const on = b.dataset.v === 'none' ? none : b.dataset.v === 'translate' ? t : p;
      b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on));
    }
    // 只润色时保持原文语言，语言选项不参与；仅编辑时所有 AI 选项都不参与
    $('srcLang').disabled = !t; $('tgtLang').disabled = !t;
    AI_FIELDS.forEach(id => $(id).disabled = none);
    document.body.classList.toggle('mode-none', none);
    $('runAllBtn').textContent = none ? '▶ 未选择 AI 处理' : t && p ? '▶ 翻译+润色全部' : p ? '▶ 润色全部' : '▶ 翻译全部';
    $('modeHint').textContent = none
      ? '不使用 AI：只调整时间轴或手动修改字幕，导出时使用原文。要翻译或润色，点上面的「翻译」或「润色」。'
      : t && p ? '先翻译，再对照原文校对润色译文。每批调用两次模型，耗时和用量约为两倍。'
        : p ? '润色原文，保持原文语言不翻译；结果写入右侧「译文」栏。'
          : '把原文翻译成目标语言。';
    const has = !!state.project;
    ['runAllBtn', 'runEmptyBtn', 'runSelBtn'].forEach(id => $(id).disabled = !has || state.running);
    applyModeLock();
    if (state.project) refreshAllRowStatus();
  }

  // ---------- 文件加载 ----------
  function initFileUI() {
    $('openBtn').addEventListener('click', () => $('fileInput').click());
    $('fileInput').addEventListener('change', (e) => { if (e.target.files[0]) loadFile(e.target.files[0]); e.target.value = ''; });
    const dz = $('dropzone');
    let dragDepth = 0;
    document.addEventListener('dragenter', (e) => { e.preventDefault(); dragDepth++; document.body.classList.add('dragging'); });
    document.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove('dragging'); } });
    document.addEventListener('dragover', (e) => e.preventDefault());
    document.addEventListener('drop', (e) => {
      e.preventDefault(); dragDepth = 0; document.body.classList.remove('dragging');
      const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) loadFile(f);
    });
    dz.addEventListener('click', () => $('fileInput').click());
  }
  async function loadFile(file) {
    if (state.project && state.project.cues.some(c => c.translation) && !confirm('当前已有编辑内容，打开新文件将替换它（所有电脑上都会换成新文件）。继续？')) return;
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await api('/api/parse', { method: 'POST', body: fd });
      const data = await r.json();
      const bilingual = SubReflow.detectBilingual(data.cues.map(c => c.text));
      data.cues.forEach(c => { c.status = ''; c.err = ''; c.text = layoutText(c.text, bilingual); });
      data.project_id = newPid();
      state.project = data;
      renderProject();
      setSync('保存中…', 'busy');
      try {
        const j = await putFullProject(data);
        sync.pid = j.project_id; sync.version = j.version; sync.needFull = false; sync.failed = false;
        resetSnap(); setSync('已保存到服务器', 'ok');
      } catch (err) {
        sync.pid = data.project_id; sync.needFull = true; resetSnap();
        setSync('⚠ 未保存到服务器，正在重试', 'err'); sync.retry = setTimeout(() => flushProject(), 5000);
      }
      toast(`已加载 ${data.count} 条字幕（${data.format.toUpperCase()}）` +
        (bilingual ? (state.task.line_merge === false ? '。检测到双语字幕，按原样保留' : '。检测到双语字幕，已按语言分行显示') +
          (isEditOnly() ? '' : '；如只需调整时间轴，可把模式切换为「仅编辑」') : ''), 'ok');
    } catch (e) { toast('加载失败：' + e.message, 'err'); }
  }

  // ---------- 渲染 ----------
  function renderProject() {
    const p = state.project; const grid = $('grid'); grid.innerHTML = ''; rows.clear();
    const has = !!p;
    $('dropzone').hidden = has; $('tableTools').hidden = !has; $('gridHead').hidden = !has;
    ['exportBtn', 'runAllBtn', 'runEmptyBtn', 'runSelBtn', 'shiftBtn', 'findBtn', 'reflowBtn', 'swapBtn', 'clearTransBtn', 'retryErrBtn'].forEach(id => $(id).disabled = !has);
    applyModeLock();
    if (!has) { $('fileInfo').textContent = '未加载文件 · 支持 SRT / VTT / ASS / SSA'; return; }
    $('fileInfo').textContent = `${p.filename || '未命名'} · ${p.format.toUpperCase()} · ${p.cues.length} 条`;
    const frag = document.createDocumentFragment();
    for (const cue of p.cues) frag.appendChild(buildRow(cue));
    grid.appendChild(frag);
    requestAnimationFrame(() => grid.querySelectorAll('textarea').forEach(autoGrow));
    updateCounter(); applyFilter();
  }
  function buildRow(cue) {
    const row = document.createElement('div');
    row.className = 'row'; row.dataset.id = cue.id;
    row.innerHTML = `
      <div class="chk-cell"><input type="checkbox" class="sel"></div>
      <div class="num">${cue.id}</div>
      <div class="time">
        <input class="t-start" value="${msToTime(cue.start)}" title="开始时间">
        <input class="t-end" value="${msToTime(cue.end)}" title="结束时间">
        <span class="dur"></span>
      </div>
      <div class="orig-cell"><textarea class="orig" rows="1" spellcheck="false"></textarea></div>
      <div class="trans-cell"><textarea class="trans" rows="1" spellcheck="false" placeholder="（待翻译）"></textarea></div>
      <div class="actions">
        <button class="btn icon ai" title="用 AI 重新生成这一行">AI</button>
        <span class="st"></span>
      </div>`;
    const orig = row.querySelector('.orig'), trans = row.querySelector('.trans');
    orig.value = cue.text; trans.value = cue.translation || '';
    orig.addEventListener('input', () => { cue.text = orig.value; autoGrow(orig); saveProject(); });
    trans.addEventListener('input', () => { cue.translation = trans.value; cue.status = trans.value ? 'edited' : ''; cue.err = ''; autoGrow(trans); applyRowStatus(row, cue); updateCounter(); saveProject(); });
    trans.addEventListener('keydown', (e) => {
      // Enter 跳到下一行译文，Shift+Enter 换行
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); const next = row.nextElementSibling; if (next) { const t = next.querySelector('.trans'); t.focus(); t.select(); } }
    });
    const ts = row.querySelector('.t-start'), te = row.querySelector('.t-end');
    const onTime = (inp, key) => () => {
      const v = timeToMs(inp.value);
      if (v === null) { inp.classList.add('bad'); return; }
      inp.classList.remove('bad'); cue[key] = v; inp.value = msToTime(v); updateDur(row, cue); saveProject();
    };
    ts.addEventListener('change', onTime(ts, 'start')); te.addEventListener('change', onTime(te, 'end'));
    row.querySelector('.sel').addEventListener('change', (e) => { row.classList.toggle('selected', e.target.checked); updateCounter(); });
    row.querySelector('.ai').addEventListener('click', () => runTranslate([cue], true));
    row.querySelector('.st').addEventListener('click', () => { if (cue.err) alert(cue.err); });
    updateDur(row, cue); applyRowStatus(row, cue);
    rows.set(cue.id, row);
    return row;
  }
  function updateDur(row, cue) {
    const d = cue.end - cue.start;
    const el = row.querySelector('.dur');
    el.textContent = (d / 1000).toFixed(2) + 's'; el.style.color = d <= 0 ? 'var(--err)' : '';
  }
  function applyRowStatus(row, cue) {
    // 歌词 / 氛围音：没有译文、或者之前翻译失败的，显示为「不翻译」
    const skipped = !isEditOnly() && isSkip(cue) && !(cue.translation || '').trim() && (!cue.status || cue.status === 'error');
    const stName = skipped ? 'skipped' : cue.status;
    row.className = 'row' + (row.classList.contains('selected') ? ' selected' : '') + (row.classList.contains('hidden') ? ' hidden' : '') + (stName ? ' st-' + stName : '');
    const st = row.querySelector('.st');
    st.textContent = skipped ? '♪ 不翻译' : ({ queued: '排队中', pending: '处理中…', done: '✓ AI', error: '✗ 失败', edited: '✎ 已编辑' }[cue.status] || '');
    st.title = skipped ? '歌词或氛围音，按「字幕排版」里的设置不翻译，导出时显示原文' : (cue.err || '');
    row.querySelector('.trans').placeholder = isEditOnly() ? '（不翻译，可手动填写）' : skipped ? '（歌词 / 氛围音，不翻译）' : '（待翻译）';
  }
  function refreshRow(cue) {
    const row = rows.get(cue.id); if (!row) return;
    const t = row.querySelector('.trans');
    if (t.value !== (cue.translation || '')) { t.value = cue.translation || ''; autoGrow(t); }
    applyRowStatus(row, cue);
  }
  function updateCounter() {
    if (!state.project) return;
    const cues = state.project.cues;
    const done = cues.filter(c => c.translation.trim()).length, sel = selectedCues().length;
    const err = cues.filter(c => c.status === 'error' && !isSkip(c)).length;
    const skipped = skipLyricsOn() && !isEditOnly() ? cues.filter(c => !c.translation.trim() && isSkip(c)).length : 0;
    $('counter').textContent = `共 ${cues.length} 条 · 已有译文 ${done} · 失败 ${err}` + (skipped ? ` · 歌词/音效不翻译 ${skipped}` : '') + (sel ? ` · 选中 ${sel}` : '');
    $('retryErrBtn').disabled = !err || state.running || isEditOnly();
  }
  function selectedCues() {
    if (!state.project) return [];
    return state.project.cues.filter(c => { const r = rows.get(c.id); return r && r.querySelector('.sel').checked; });
  }
  function applyFilter() {
    if (!state.project) return;
    const onlyEmpty = $('onlyEmpty').checked, onlyErr = $('onlyErr').checked, q = $('filterText').value.trim().toLowerCase();
    for (const cue of state.project.cues) {
      const row = rows.get(cue.id);
      let show = true;
      if (onlyEmpty && (cue.translation.trim() || isSkip(cue))) show = false;
      if (onlyErr && (cue.status !== 'error' || isSkip(cue))) show = false;
      if (q && !(cue.text.toLowerCase().includes(q) || (cue.translation || '').toLowerCase().includes(q))) show = false;
      row.classList.toggle('hidden', !show);
    }
  }

  // ---------- 翻译执行 ----------
  function taskPayload() {
    const t = state.task;
    return { mode: t.mode, source_lang: t.source_lang, target_lang: t.target_lang, tone: t.tone, glossary: t.glossary, instructions: t.instructions,
      single_line: t.line_merge !== false, skip_lyrics: t.skip_lyrics !== false };
  }
  // ---------- 歌词 / 氛围音 ----------
  const lyricCache = new Map();
  function isLyric(text) {
    if (!lyricCache.has(text)) { if (lyricCache.size > 20000) lyricCache.clear(); lyricCache.set(text, SubReflow.isLyricOrSfx(text)); }
    return lyricCache.get(text);
  }
  function skipLyricsOn() { return state.task.skip_lyrics !== false; }
  // 开启「忽略歌词和氛围音」时，这条不送去翻译
  function isSkip(c) { return skipLyricsOn() && isLyric(c.text); }
  function refreshAllRowStatus() {
    if (!state.project) return;
    for (const c of state.project.cues) { const r = rows.get(c.id); if (r) applyRowStatus(r, c); }
    updateCounter(); applyFilter();
  }
  // ---------- 字幕排版 ----------
  function lineWidth() { return Math.min(120, Math.max(16, parseInt(state.task.line_width) || 50)); }
  // 开启「多行合并」时：合并为一行，超过宽度上限再按断句换行
  // bilingual：null（单语）| 'lines' | 'words'，见 SubReflow.detectBilingual。双语字幕按语言分成两行，各自合并、超宽才换行
  function layoutText(text, bilingual) {
    if (state.task.line_merge === false) return text;
    return bilingual ? SubReflow.reflowBilingual(text, lineWidth(), bilingual) : SubReflow.reflow(text, lineWidth());
  }
  function updateLineWidthHint() {
    const w = lineWidth();
    $('lineWidthHint').textContent = `约 ${Math.floor(w / 2)} 个汉字 / ${w} 个字母`;
    $('lineWidth').disabled = state.task.line_merge === false;
  }
  function providerPayload() {
    const s = activeProfile();
    return { profile_id: s.id, api_key: '', base_url: s.base_url, model: s.model, temperature: s.temperature, timeout: s.timeout, json_mode: s.json_mode, thinking: s.thinking || 'off' };
  }
  async function runTranslate(targets, single = false) {
    if (!state.project) return;
    if (isEditOnly()) { toast('当前是「仅编辑」模式，不使用 AI。要翻译或润色，先在左侧选择「翻译」或「润色」'); return; }
    targets = targets.filter(c => c.text.trim());
    let skippedN = 0;
    if (!single && skipLyricsOn()) {
      const before = targets.length;
      targets = targets.filter(c => !isSkip(c));
      skippedN = before - targets.length;
      if (!targets.length && skippedN) { toast(`这 ${skippedN} 条都是歌词或氛围音，已跳过（可在「字幕排版」里关闭）`); return; }
    }
    if (!targets.length) { toast('没有需要处理的条目'); return; }
    if (state.running && !single) { toast('已有任务在运行'); return; }
    const cues = state.project.cues;
    const indexOf = new Map(cues.map((c, i) => [c.id, i]));
    const size = single ? 1 : state.task.batch_size, conc = single ? 1 : state.task.concurrency;
    const batches = [];
    for (let i = 0; i < targets.length; i += size) batches.push(targets.slice(i, i + size));

    const controller = new AbortController();
    if (!single) { state.running = true; state.abort = controller; setRunningUI(true); setStatus($('statusMsg'), '使用模型：' + profileLabel(activeProfile()) + (skippedN ? `\n已跳过 ${skippedN} 条歌词或氛围音` : '')); }
    // 先全部标为「排队中」，轮到某一批时才标为「处理中」
    targets.forEach(c => { c._prev = c.status; c.status = single ? 'pending' : 'queued'; c.err = ''; refreshRow(c); });
    updateCounter();

    let done = 0, failed = 0, next = 0, warned = 0, lastWarn = '', slowHinted = false;
    const total = targets.length;
    const inflight = new Map(); // 批次序号 -> 开始时间
    const tick = (msg) => {
      if (single) return;
      $('progressBar').style.width = Math.round((done + failed) / total * 100) + '%';
      if (msg) { $('progressText').textContent = msg; return; }
      let text = `已完成 ${done}/${total}${failed ? ` · 失败 ${failed}` : ''}`;
      if (inflight.size) {
        const waited = Math.round((Date.now() - Math.min(...inflight.values())) / 1000);
        text += ` · 进行中 ${inflight.size} 批（第 ${Math.min(next, batches.length)}/${batches.length} 批，已等待 ${waited}s）`;
        if (waited >= 45 && !slowHinted) {
          slowHinted = true;
          const p = activeProfile();
          setStatus($('statusMsg'), `使用模型：${profileLabel(p)}\n模型响应较慢（单批已等待 ${waited} 秒）。` +
            (p && p.thinking && p.thinking !== 'off' ? `当前深度思考为「${THINKING_LABELS[p.thinking] || p.thinking}」，可在模型设置里调低或关闭；` : '') +
            `也可以停止后调小每批条数。超过 ${p ? p.timeout : 120} 秒未完成的批次会标记为失败，可稍后重试。`);
        }
      }
      $('progressText').textContent = text;
    };
    const ticker = single ? null : setInterval(() => tick(), 1000);
    tick();

    async function worker() {
      while (next < batches.length && !controller.signal.aborted) {
        const bi = next++;
        const batch = batches[bi];
        inflight.set(bi, Date.now());
        batch.forEach(c => { c.status = 'pending'; refreshRow(c); });
        tick();
        const firstIdx = indexOf.get(batch[0].id), lastIdx = indexOf.get(batch[batch.length - 1].id);
        const before = cues.slice(Math.max(0, firstIdx - CONTEXT_N), firstIdx).map(c => c.text);
        const after = cues.slice(lastIdx + 1, lastIdx + 1 + CONTEXT_N).map(c => c.text);
        try {
          const r = await api('/api/translate', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
            body: JSON.stringify({ provider: providerPayload(), task: taskPayload(), items: batch.map(c => ({ id: c.id, text: c.text })), context: { before, after } }),
          });
          const j = await r.json();
          if (j.warning) { warned++; lastWarn = j.warning; if (!single) setStatus($('statusMsg'), '提示：' + j.warning); }
          const map = new Map(j.results.map(x => [x.id, x.text]));
          for (const c of batch) {
            if (map.has(c.id)) { c.translation = layoutText(map.get(c.id)); c.status = 'done'; done++; }
            else { c.status = 'error'; c.err = '未返回该条结果'; failed++; }
            refreshRow(c);
          }
        } catch (e) {
          const aborted = e.name === 'AbortError';
          const msg = aborted ? '已停止' : e.message;
          for (const c of batch) {
            if (aborted) { c.status = c._prev || ''; c.err = ''; }
            else { c.status = 'error'; c.err = msg; failed++; }
            refreshRow(c);
          }
          if (e.name !== 'AbortError') setStatus($('statusMsg'), '最近错误：' + explainError(msg), 'err');
        }
        inflight.delete(bi);
        tick(); updateCounter(); saveProject();
      }
    }
    await Promise.all(Array.from({ length: Math.min(conc, batches.length) }, worker));
    if (ticker) clearInterval(ticker);
    // 停止后仍在排队的条目恢复原状态
    targets.forEach(c => { if (c.status === 'queued' || c.status === 'pending') { c.status = c._prev || ''; refreshRow(c); } delete c._prev; });

    if (!single) {
      state.running = false; state.abort = null; setRunningUI(false);
      const aborted = controller.signal.aborted;
      tick(aborted ? `已停止 · 完成 ${done}/${total}${failed ? ` · 失败 ${failed}` : ''}` : undefined);
      if (!aborted) {
        let msg = (failed ? `完成，${failed} 条失败，可点「重试失败项」` : `全部完成 ✓ 共 ${done} 条`) + (skippedN ? `（跳过 ${skippedN} 条歌词或氛围音）` : '');
        if (warned) msg += `\n${warned} 批提示：${lastWarn}`;
        setStatus($('statusMsg'), msg, failed || warned ? 'err' : 'ok');
        toast(failed ? `完成，${failed} 条失败` : '全部完成 ✓', failed ? 'err' : 'ok');
      }
    } else if (targets[0].status === 'done') toast(lastWarn ? '已重新生成（' + lastWarn + '）' : '已重新生成', lastWarn ? 'err' : 'ok');
    updateCounter(); saveProject();
  }
  function setRunningUI(on) {
    ['runAllBtn', 'runEmptyBtn', 'runSelBtn', 'reflowBtn', 'swapBtn', 'clearTransBtn'].forEach(id => $(id).disabled = on);
    $('stopBtn').hidden = !on; $('progressWrap').hidden = false; $('activeProfile').disabled = on;
    if (on) { $('progressBar').style.width = '0%'; setStatus($('statusMsg'), ''); }
    applyModeLock();
  }

  // ---------- 导出 ----------
  function initExportUI() {
    $('exportBtn').addEventListener('click', openExport);
    $('exportCancel').addEventListener('click', () => $('exportDlg').close());
    $('exportFormat').addEventListener('change', updateExportFontUI);
    initExportFontControls();
    initFontSelects();
    $('exportContent').addEventListener('change', updateExportFontUI);
    $('exportOk').addEventListener('click', doExport);
    // 参数一改就保存：取消、刷新或换字幕后都沿用
    ['input', 'change'].forEach(ev => $('exportDlg').addEventListener(ev, (e) => saveExportPrefs(e.target.id)));
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (state.project) openExport(); }
    });
  }
  // ---------- 导出：分辨率与字号 ----------
  // 字号按画面高度换算：保存的 font_size 是 1080p 下的字号，720p 约为 2/3，4K 为 2 倍，
  // 三种分辨率在画面上的相对大小相同。导入的 ASS 沿用它自己的画布，字号按比例缩放它的样式。
  const RES_LIST = [720, 1080, 2160];
  const exp = { res: 1080, base: 44, imported: false, importedSize: 0 };
  function detectRes(name) {
    const n = String(name || '');
    if (/(^|[^0-9])(2160p|4k|uhd)([^a-z0-9]|$)/i.test(n)) return 2160;
    if (/(^|[^0-9])1080[pi]([^a-z0-9]|$)/i.test(n)) return 1080;
    if (/(^|[^0-9])720p([^a-z0-9]|$)/i.test(n)) return 720;
    return null;
  }
  // ---------- ASS 字体与颜色 ----------
  // 主字幕：译文，或双语原文件的第一种语言；副字幕：双语导出时的原文，或双语原文件的第二种语言
  // ASS 文件里只写一个字体名，播放设备上要装有同名字体。按平台分组，方便选播放设备上有的字体。
  // 只保留大多数设备都有的字体，以及苹果设备（macOS / iOS）自带的中文字体
  const ASS_FONT_GROUPS = [
    ['通用（大多数设备都有）', [['Arial', 'Arial'], ['Helvetica', 'Helvetica'], ['Verdana', 'Verdana'], ['Times New Roman', 'Times New Roman']]],
    ['苹果设备（macOS / iOS）', [['PingFang SC', '苹方 PingFang SC'], ['Heiti SC', '黑体 Heiti SC'], ['Kaiti SC', '楷体 Kaiti SC'], ['Songti SC', '宋体 Songti SC'], ['Hiragino Sans GB', '冬青黑体 Hiragino Sans GB']]],
  ];
  // 旧版本保存的、或导入的 ASS 里用的已移除字体：换成对应的苹果字体
  const FONT_REPLACE = {
    'KaiTi': 'Kaiti SC', '楷体': 'Kaiti SC', 'STKaiti': 'Kaiti SC', 'SimHei': 'Heiti SC', '黑体': 'Heiti SC', 'STHeiti': 'Heiti SC',
    'SimSun': 'Songti SC', '宋体': 'Songti SC', 'STSong': 'Songti SC', 'Microsoft YaHei': 'PingFang SC', '微软雅黑': 'PingFang SC',
    'Noto Sans CJK SC': 'PingFang SC', 'Noto Sans SC': 'PingFang SC', 'Source Han Sans SC': 'PingFang SC', 'Source Han Sans CN': 'PingFang SC',
    'Helvetica Neue': 'Helvetica',
  };
  const ASS_FONTS = ASS_FONT_GROUPS.flatMap(([, list]) => list);
  // 预览用：本机没有所选字体时，找同一款字体在其他系统上的名字（same），或近似的字体（near）
  const FONT_ALIASES = {
    'KaiTi': { same: ['楷体', 'Kaiti SC', 'STKaiti', 'BiauKai'] }, 'Kaiti SC': { same: ['STKaiti', 'KaiTi', '楷体'] },
    'SimHei': { same: ['黑体', 'Heiti SC', 'STHeiti'] }, 'Heiti SC': { same: ['STHeiti', 'SimHei', '黑体'] },
    'SimSun': { same: ['宋体', 'Songti SC', 'STSong'] }, 'Songti SC': { same: ['STSong', 'SimSun', '宋体'] },
    'Source Han Sans SC': { same: ['Source Han Sans CN', '思源黑体', 'Noto Sans CJK SC', 'Noto Sans SC'], near: ['PingFang SC', 'Microsoft YaHei'] },
    'Noto Sans CJK SC': { same: ['Noto Sans SC', 'Source Han Sans SC', 'Source Han Sans CN'], near: ['PingFang SC', 'Microsoft YaHei'] },
    'Microsoft YaHei': { same: ['微软雅黑'], near: ['PingFang SC', 'Hiragino Sans GB', 'Noto Sans CJK SC'] },
    'PingFang SC': { near: ['Microsoft YaHei', 'Hiragino Sans GB', 'Noto Sans CJK SC'] },
    'Hiragino Sans GB': { near: ['PingFang SC', 'Microsoft YaHei'] },
    'Helvetica': { same: ['Helvetica Neue'], near: ['Arial'] }, 'Arial': { near: ['Helvetica', 'Liberation Sans'] },
  };
  // 本机是否装有某个字体：用画布量同一段文字，换成该字体后宽度有变化就说明装了
  const fontCache = new Map();
  function fontInstalled(name) {
    if (!name) return false;
    if (fontCache.has(name)) return fontCache.get(name);
    const ctx = (fontInstalled.ctx = fontInstalled.ctx || document.createElement('canvas').getContext('2d'));
    const sample = '要是大家知道 If people knew 永和9';
    const ok = ['monospace', 'serif', 'sans-serif'].some(base => {
      ctx.font = `40px ${base}`; const w0 = ctx.measureText(sample).width;
      ctx.font = `40px "${name.replace(/"/g, '')}", ${base}`; return ctx.measureText(sample).width !== w0;
    });
    fontCache.set(name, ok);
    return ok;
  }
  // 预览实际使用的字体：{ family: CSS 字体名, kind: 'exact' | 'same' | 'near' | 'none', used: 实际用的字体名 }
  function previewFont(name) {
    if (fontInstalled(name)) return { family: name, kind: 'exact', used: name };
    const a = FONT_ALIASES[name] || {};
    for (const [kind, list] of [['same', a.same || []], ['near', a.near || []]]) {
      const hit = list.find(fontInstalled);
      if (hit) return { family: hit, kind, used: hit };
    }
    return { family: name, kind: 'none', used: '' };
  }
  function fontLabel(name) { const f = ASS_FONTS.find(([v]) => v === name); return f ? f[1] : name; }
  const DEFAULT_ASS_STYLE = { font: 'Arial', color: '#FFFFFF', outline_color: '#000000', font2: 'Arial', color2: '#FFFFFF', outline_color2: '#000000' };
  const FONT_CTRL = { font: 'assFont', font2: 'assFont2' };
  const COLOR_CTRL = { color: 'assColor', outline_color: 'assOutline', color2: 'assColor2', outline_color2: 'assOutline2' };
  function fontOptions(extra) {
    // extra：导入的 ASS 里不在列表中的字体，单独列出，选它就保留原文件的字体
    const head = extra ? `<optgroup label="原文件"><option value="${escHtml(extra)}">原文件字体：${escHtml(extra)}${fontInstalled(extra) ? '' : ' · 本机未装'}</option></optgroup>` : '';
    return head + ASS_FONT_GROUPS.map(([g, list]) => `<optgroup label="${escHtml(g)}">` +
      list.map(([v, l]) => `<option value="${escHtml(v)}">${escHtml(l)}${fontInstalled(v) ? '' : ' · 本机未装'}</option>`).join('') + '</optgroup>').join('');
  }
  function initFontSelects() {
    for (const selId of Object.values(FONT_CTRL)) {
      $(selId).innerHTML = fontOptions('');
      $(selId).addEventListener('change', updateAssPreview);
    }
    Object.values(COLOR_CTRL).forEach(id => $(id).addEventListener('input', updateAssPreview));
    $('assStyleReset').addEventListener('click', () => { setAssStyle(exp.imported ? exp.fileStyle : DEFAULT_ASS_STYLE); updateExportFontUI(); saveExportPrefs('assStyleReset'); });
  }
  // 设置下拉框：列表里有就直接选；已移除的字体换成对应的苹果字体；
  // 导入的 ASS 里其他字体作为「原文件字体」保留；其余用 Arial
  function setFontCtrl(key, font) {
    const sel = $(FONT_CTRL[key]);
    font = String(font || '').trim();
    const known = (f) => ASS_FONTS.some(([v]) => v === f);
    const original = exp.fileStyle?.[key];
    const extra = original && !known(original) && !Object.hasOwn(FONT_REPLACE, original) ? original : '';
    if (!known(font) && font !== extra) font = Object.hasOwn(FONT_REPLACE, font) ? FONT_REPLACE[font] : 'Arial';
    sel.innerHTML = fontOptions(extra);
    sel.value = font;
  }
  function getFontCtrl(key) { return $(FONT_CTRL[key]).value.replace(/[,{}\\]/g, '').trim(); }
  function setAssStyle(st) {
    const v = { ...DEFAULT_ASS_STYLE, ...(st || {}) };
    setFontCtrl('font', v.font); setFontCtrl('font2', v.font2);
    for (const [k, id] of Object.entries(COLOR_CTRL)) $(id).value = /^#[0-9a-f]{6}$/i.test(v[k]) ? v[k].toLowerCase() : DEFAULT_ASS_STYLE[k].toLowerCase();
  }
  function getAssStyle() {
    const out = { font: getFontCtrl('font') || 'Arial', font2: getFontCtrl('font2') || 'Arial' };
    for (const [k, id] of Object.entries(COLOR_CTRL)) out[k] = $(id).value.toUpperCase();
    return out;
  }
  // ASS 颜色 &HAABBGGRR（或 SSA 的十进制）→ #RRGGBB
  function assColorToHex(v) {
    v = String(v || '').trim();
    let n;
    if (/^&H/i.test(v)) n = parseInt(v.slice(2).replace(/&$/, '') || '0', 16); else n = parseInt(v, 10);
    if (isNaN(n)) return null;
    const r = n & 255, g = (n >> 8) & 255, b = (n >> 16) & 255;
    return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('').toUpperCase();
  }
  // 当前导出时，副字幕是否存在：双语导出，或原文件本身是双语字幕且导出「仅原文」
  function secondaryActive() {
    const c = $('exportContent').value;
    return c.startsWith('bilingual') || (c === 'original' && !!exp.bilingualSource);
  }
  // 预览用的示例文字
  function previewSample() {
    const p = state.project, content = $('exportContent').value;
    const clean = (t) => String(t || '').replace(/<[^>]+>|\{\\[^}]*\}/g, '').trim();
    if (p) {
      if (content !== 'original') {
        const c = p.cues.find(x => (x.translation || '').trim() && x.text.trim() && !isLyric(x.text));
        if (c) return content === 'bilingual_ot' ? { top: clean(c.text), bottom: clean(c.translation), secTop: true }
          : content === 'bilingual_to' ? { top: clean(c.translation), bottom: clean(c.text), secTop: false } : { top: clean(c.translation), bottom: '', secTop: false };
      }
      if (exp.bilingualSource && content !== 'translation') {
        const c = p.cues.find(x => !isLyric(x.text) && SubReflow.splitBilingual(x.text, true));
        if (c) { const [a, b] = SubReflow.splitBilingual(c.text, true); return { top: clean(a), bottom: clean(b), secTop: false }; }
      }
      const c = p.cues.find(x => x.text.trim() && !isLyric(x.text));
      if (c) return { top: clean(c.text), bottom: '', secTop: false };
    }
    return { top: '示例字幕', bottom: 'Sample subtitle', secTop: false };
  }
  function outlineShadow(color, px) {
    const d = [];
    for (let a = 0; a < 16; a++) { const r = a * Math.PI / 8; d.push(`${(Math.cos(r) * px).toFixed(2)}px ${(Math.sin(r) * px).toFixed(2)}px 0 ${color}`); }
    return d.join(',') + `, 0 ${px * 0.9}px ${px * 1.4}px rgba(0,0,0,.55)`;
  }
  function updateAssPreview() {
    const ass = $('exportFormat').value === 'ass';
    const st = getAssStyle(), sec = secondaryActive();
    const sample = previewSample();
    const fs = parseInt($('exportFontSize').value) || 44, sc = (parseInt($('exportScale2').value) || 75) / 100;
    // 预览框里的字号：按字号占画面高度的比例换算（预览高度约代表画面下方的一段）
    const ref = exp.imported ? (exp.playY || 1080) : exp.res;
    const mainPx = Math.max(12, Math.min(34, fs / ref * 560));
    const look = (el, font, color, outline, px) => {
      const pf = previewFont(font);
      el.style.fontFamily = `"${pf.family}", "PingFang SC", "Microsoft YaHei", sans-serif`;
      el.style.color = color; el.style.fontSize = px + 'px';
      el.style.textShadow = outlineShadow(outline, Math.max(1, px * 0.07));
      return pf;
    };
    const top = $('pvTop'), bottom = $('pvBottom');
    const secText = sec ? (sample.secTop ? sample.top : sample.bottom) : '';
    const mainText = sec ? (sample.secTop ? sample.bottom : sample.top) : sample.top;
    const [mainEl, secEl] = sample.secTop && sec ? [bottom, top] : [top, bottom];
    mainEl.textContent = mainText; secEl.textContent = secText;
    const notes = [];
    const describe = (role, font, pf) => {
      if (pf.kind === 'exact') return;
      const name = `「${fontLabel(font)}」`;
      if (pf.kind === 'same') notes.push(`${role}：本机没有 ${name}，预览用的是同一款字体「${pf.used}」。`);
      else if (pf.kind === 'near') notes.push(`${role}：本机没有 ${name}，预览用近似的「${pf.used}」，实际效果以播放设备为准。`);
      else notes.push(`${role}：本机没有 ${name}，预览只能用默认字体。`);
    };
    if (ass) {
      describe('主字幕', st.font, look(mainEl, st.font, st.color, st.outline_color, mainPx));
      const pf2 = look(secEl, st.font2, st.color2, st.outline_color2, mainPx * sc);
      if (sec) describe('副字幕', st.font2, pf2);
    } else {
      look(mainEl, st.font, st.color, st.outline_color, mainPx); look(secEl, st.font2, st.color2, st.outline_color2, mainPx * sc);
    }
    $('assFontNote').textContent = notes.join('\n');
    $('assPreview').classList.toggle('off', !ass);
  }
  // 导入的 ASS：对白里用得最多的样式的字号、字体、颜色，及其画布大小
  function assHeaderInfo(p) {
    const info = { size: 0, playX: 0, playY: 0, font: '', color: null, outline: null };
    if (!p || !p.header) return info;
    let fmt = [], inStyles = false; const sizes = {}, rowsByName = {};
    for (const raw of p.header.split('\n')) {
      const t = raw.trim(), low = t.toLowerCase();
      const m = t.match(/^PlayRes([XY])\s*:\s*(\d+)/i);
      if (m) info['play' + m[1].toUpperCase()] = parseInt(m[2]);
      if (t.startsWith('[')) { inStyles = /^\[v4\+? styles\+?\]$/i.test(t); continue; }
      if (!inStyles) continue;
      if (low.startsWith('format:')) fmt = t.slice(7).split(',').map(x => x.trim().toLowerCase());
      else if (low.startsWith('style:') && fmt.length) {
        const v = t.slice(6).split(',').map(x => x.trim());
        const size = parseFloat(v[fmt.indexOf('fontsize')]);
        if (!isNaN(size)) sizes[v[fmt.indexOf('name')]] = size;
        const oc = fmt.includes('outlinecolour') ? 'outlinecolour' : 'tertiarycolour';
        rowsByName[v[fmt.indexOf('name')]] = { font: v[fmt.indexOf('fontname')] || '', color: v[fmt.indexOf('primarycolour')], outline: v[fmt.indexOf(oc)] };
      }
    }
    const counts = {};
    for (const c of p.cues) { const st = c.meta && c.meta.Style; if (st && (c.meta.kind || 'Dialogue') === 'Dialogue') counts[st] = (counts[st] || 0) + 1; }
    let main = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).find(n => n in sizes);
    if (!main) main = 'Default' in sizes ? 'Default' : Object.keys(sizes)[0];
    info.size = Math.round(sizes[main] || 0);
    const row = rowsByName[main] || {};
    info.font = row.font || ''; info.color = assColorToHex(row.color); info.outline = assColorToHex(row.outline);
    return info;
  }
  function openExport() {
    const p = state.project; if (!p) return;
    const base = (p.filename || 'subtitle').replace(/\.[^.]+$/, '');
    const tgt = state.task.target_lang.replace(/[（(].*?[)）]/g, m => m).replace(/[\/\\:*?"<>|]/g, '');
    const hasTr = p.cues.some(c => (c.translation || '').trim());
    $('exportName').value = hasTr ? `${base}.${tgt}` : base;
    // 格式和内容沿用上次的选择；没有译文时只能导出原文（不改动保存的选择）
    $('exportContent').value = hasTr ? (state.task.export_content || 'translation') : 'original';
    $('exportFormat').value = state.task.export_format || (p.format === 'ssa' ? 'ass' : (['srt', 'vtt', 'ass'].includes(p.format) ? p.format : 'srt'));
    const hi = assHeaderInfo(p);
    exp.imported = ['ass', 'ssa'].includes(p.format) && !!p.header && hi.size > 0;
    exp.importedSize = hi.size; exp.playX = hi.playX; exp.playY = hi.playY;
    exp.base = state.task.font_size || 44;
    exp.res = detectRes(p.filename) || (RES_LIST.includes(+state.task.export_res) ? +state.task.export_res : 1080);
    exp.detected = !!detectRes(p.filename);
    $('exportRes').value = exp.res;
    $('exportFontSize').value = exp.imported ? exp.importedSize
      : Math.min(240, Math.max(10, Math.round(exp.base * exp.res / 1080)));
    $('exportScale2').value = state.task.font_scale2 || 75;
    exp.bilingualSource = SubReflow.detectBilingual(p.cues.map(c => c.text));
    // 导入的 ASS 默认用原文件样式，用户修改过的项目用保存的偏好覆盖
    exp.fileStyle = exp.imported ? { font: hi.font || 'Arial', color: hi.color || '#FFFFFF', outline_color: hi.outline || '#000000',
      font2: hi.font || 'Arial', color2: hi.color || '#FFFFFF', outline_color2: hi.outline || '#000000' } : null;
    setAssStyle(exp.imported ? { ...exp.fileStyle, ...state.task.ass_import_style } : state.task.ass_style);
    updateExportFontUI();
    $('exportDlg').showModal();
  }
  function updateExportFontUI() {
    const ass = $('exportFormat').value === 'ass', bi = $('exportContent').value.startsWith('bilingual');
    const imported = ass && exp.imported;
    // SRT / VTT 不包含字号：三个选项都不可用；导入的 ASS 沿用原画布，分辨率不可改
    $('exportRes').disabled = !ass || imported;
    $('exportFontSize').disabled = !ass;
    const sec = ass && secondaryActive();
    $('exportScale2').disabled = !sec;
    ['assFont', 'assColor', 'assOutline', 'assStyleReset'].forEach(id => $(id).disabled = !ass);
    ['assFont2', 'assColor2', 'assOutline2'].forEach(id => $(id).disabled = !sec);
    document.querySelectorAll('.as-l.sec').forEach(el => el.classList.toggle('off', !sec));
    const fs = parseInt($('exportFontSize').value) || 0, sc = parseInt($('exportScale2').value) || 75;
    let tip;
    if (!ass) tip = `${$('exportFormat').value === 'vtt' ? 'WebVTT' : 'SRT'} 文件不包含字号，由播放器决定。要设置分辨率和字号，请把格式改为 ASS。`;
    else if (imported) tip = `原文件是 ASS，沿用它自己的画布（${exp.playX || '?'}×${exp.playY || '?'}）。修改字号会按比例缩放它的全部样式（原来是 ${exp.importedSize}）。`;
    else {
      const pct = fs ? (fs / exp.res * 100).toFixed(1) : '?';
      tip = `${exp.res === 2160 ? '4K' : exp.res + 'p'} 画面，字号 ${fs || '?'}（约为画面高度的 ${pct}%）。` +
        (exp.detected ? '分辨率按文件名自动选择。' : '') + '换分辨率时字号自动换算，画面上大小不变；想更大或更小，直接改字号。';
    }
    if (ass) tip += sec ? `副字幕按主字幕的 ${sc}% 显示。` : '';
    $('exportFontTip').textContent = tip;
    $('assStyleTip').textContent = !ass ? 'SRT / WebVTT 不包含字体和颜色，由播放器决定。'
      : (sec ? (bi ? '主字幕是译文，副字幕是原文。' : '原文件是双语字幕：主字幕是第一种语言，副字幕是第二种语言。')
        : '副字幕只在导出双语字幕，或原文件本身是双语字幕时使用。') +
        '「通用」字体在大多数设备上都有；「苹果设备」字体在 Mac、iPhone、iPad 上自带，其他设备上播放器会换成默认字体。' + (exp.imported ? '原文件是 ASS：这里改的是它的主要样式。' : '');
    updateAssPreview();
  }
  function initExportFontControls() {
    $('exportRes').addEventListener('change', () => {
      const fs = parseInt($('exportFontSize').value);
      if (fs > 0) exp.base = fs * 1080 / exp.res;     // 以当前字号为准换算
      exp.res = parseInt($('exportRes').value); exp.detected = false;
      if (!exp.imported) $('exportFontSize').value = Math.min(240, Math.max(10, Math.round(exp.base * exp.res / 1080)));
      updateExportFontUI();
    });
    $('exportFontSize').addEventListener('input', () => {
      const fs = parseInt($('exportFontSize').value);
      if (fs > 0 && !exp.imported) exp.base = Math.min(240, Math.max(10, fs)) * 1080 / exp.res;
      updateExportFontUI();
    });
    $('exportScale2').addEventListener('input', updateExportFontUI);
    $('exportContent').addEventListener('change', updateAssPreview);
  }
  // 导入 ASS 的字号以原文件为准，字体和颜色只保存用户改过的项目。
  // 格式和内容只在用户亲自改动时保存（id 是被改动的控件），打开时的自动选择不算。
  function saveExportPrefs(id) {
    const ass = $('exportFormat').value === 'ass';
    const sc = parseInt($('exportScale2').value);
    const changed = {};
    if (id === 'exportFormat') changed.export_format = $('exportFormat').value;
    if (id === 'exportContent') changed.export_content = $('exportContent').value;
    if (id === 'exportScale2' && sc > 0) changed.font_scale2 = Math.min(100, Math.max(40, sc));
    if (ass && exp.imported) {
      const key = Object.keys(DEFAULT_ASS_STYLE).find(k => (FONT_CTRL[k] || COLOR_CTRL[k]) === id);
      if (key) {
        const style = { ...state.task.ass_import_style };
        const value = getAssStyle()[key];
        // 「原文件字体」随字幕文件变化，不能当作通用的字体偏好保存。
        if (FONT_CTRL[key] && !ASS_FONTS.some(([font]) => font === value)) delete style[key];
        else style[key] = value;
        changed.ass_import_style = style;
      }
      if (id === 'assStyleReset') changed.ass_import_style = {};
    }
    if (ass && !exp.imported) {
      if (id === 'exportRes') changed.export_res = exp.res;
      if (id === 'exportFontSize') changed.font_size = exp.base;
      changed.ass_style = getAssStyle();
    }
    const diff = Object.entries(changed).some(([k, v]) => JSON.stringify(state.task[k]) !== JSON.stringify(v));
    if (diff) { Object.assign(state.task, changed); saveTask(); }
  }
  async function doExport() {
    const p = state.project;
    const ass = $('exportFormat').value === 'ass';
    const fs = Math.min(240, Math.max(10, parseInt($('exportFontSize').value) ||
      (exp.imported ? exp.importedSize : Math.round(exp.base * exp.res / 1080))));
    const sc = Math.min(100, Math.max(40, parseInt($('exportScale2').value) || state.task.font_scale2 || 75));
    const style = ass ? getAssStyle() : null;
    saveExportPrefs();
    // 原文件是双语字幕、导出「仅原文」的 ASS：把每条拆成主字幕（第一种语言）和副字幕（第二种语言），分别套用样式
    let content = $('exportContent').value;
    let cues = p.cues.map(c => ({ start: c.start, end: c.end, text: c.text, translation: c.translation, meta: c.meta }));
    if (ass && content === 'original' && exp.bilingualSource) {
      cues = cues.map(c => {
        const parts = SubReflow.splitBilingual(c.text, true);
        return parts ? { ...c, text: parts[1], translation: parts[0] } : { ...c, translation: '' };
      });
      content = 'bilingual_to';
    }
    try {
      const r = await api('/api/export', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cues, format: $('exportFormat').value, content, header: p.header || '', filename: $('exportName').value || 'subtitle',
          font_size: fs, secondary_scale: sc, play_res: exp.res, style }) });
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = ($('exportName').value || 'subtitle') + '.' + $('exportFormat').value;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      $('exportDlg').close(); toast('已导出', 'ok');
    } catch (e) { toast('导出失败：' + e.message, 'err'); }
  }

  // ---------- 工具 ----------
  function initTools() {
    $('selectAll').addEventListener('change', (e) => {
      for (const [, row] of rows) { if (row.classList.contains('hidden')) continue; row.querySelector('.sel').checked = e.target.checked; row.classList.toggle('selected', e.target.checked); }
      updateCounter();
    });
    ['onlyEmpty', 'onlyErr'].forEach(id => $(id).addEventListener('change', applyFilter));
    $('filterText').addEventListener('input', debounce(applyFilter, 200));

    // 偏移量按秒输入：可带正负号，最多 3 位小数，逗号也当作小数点
    const parseShift = (v) => {
      const t = String(v).trim().replace(/\s+/g, '').replace(/，/g, ',').replace(',', '.').replace(/^－/, '-').replace(/^＋/, '+');
      if (!/^[+-]?(\d+(\.\d{0,3})?|\.\d{1,3})$/.test(t)) return null;
      return Math.round(parseFloat(t) * 1000);
    };
    const fmtSec = (ms) => (ms > 0 ? '+' : ms < 0 ? '-' : '') + (Math.abs(ms) / 1000).toFixed(3);
    const updateShiftMsg = () => {
      const raw = $('shiftSec').value, ms = parseShift(raw);
      const list = $('shiftScope').value === 'sel' ? selectedCues() : (state.project ? state.project.cues : []);
      $('shiftSec').classList.toggle('bad', ms === null);
      if (ms === null) {
        const tooMany = /[.,]\d{4,}/.test(raw);
        setStatus($('shiftMsg'), tooMany ? '最多 3 位小数（精确到毫秒）' : '请输入秒数，例如 1.5、-0.25 或 2.125', 'err');
      } else if (!list.length) setStatus($('shiftMsg'), '没有选中任何字幕', 'err');
      else if (!ms) setStatus($('shiftMsg'), '');
      else {
        const clipped = ms < 0 ? list.filter(c => c.start + ms < 0).length : 0;
        setStatus($('shiftMsg'), `${list.length} 条字幕${ms > 0 ? '延后' : '提前'} ${(Math.abs(ms) / 1000).toFixed(3)} 秒` +
          (clipped ? `\n其中 ${clipped} 条会提前到 0 秒之前，将从 00:00:00,000 开始` : ''), clipped ? 'err' : 'ok');
      }
      $('shiftOk').disabled = ms === null || !ms || !list.length;
    };
    $('shiftBtn').addEventListener('click', () => {
      $('shiftScope').value = selectedCues().length ? 'sel' : 'all';
      updateShiftMsg(); $('shiftDlg').showModal(); $('shiftSec').select();
    });
    $('shiftSec').addEventListener('input', updateShiftMsg);
    $('shiftScope').addEventListener('change', updateShiftMsg);
    $('shiftSec').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); if (!$('shiftOk').disabled) $('shiftOk').click(); } });
    $('shiftCancel').addEventListener('click', () => $('shiftDlg').close());
    $('shiftOk').addEventListener('click', () => {
      const ms = parseShift($('shiftSec').value);
      if (ms === null || !ms) return;
      const list = $('shiftScope').value === 'sel' ? selectedCues() : state.project.cues;
      if (!list.length) return;
      for (const c of list) {
        // 提前到 0 秒之前的：保持时长不变，从 0 开始
        const dur = c.end - c.start;
        c.start = c.start + ms; c.end = c.end + ms;
        if (c.start < 0) { c.start = 0; c.end = Math.max(0, dur); }
        const row = rows.get(c.id); row.querySelector('.t-start').value = msToTime(c.start); row.querySelector('.t-end').value = msToTime(c.end); updateDur(row, c);
      }
      saveProject(); $('shiftDlg').close(); toast(`已偏移 ${list.length} 条，${fmtSec(ms)} 秒`, 'ok');
    });

    $('findBtn').addEventListener('click', () => { setStatus($('findMsg'), ''); $('findDlg').showModal(); });
    $('findCancel').addEventListener('click', () => $('findDlg').close());
    $('replaceAllBtn').addEventListener('click', () => {
      const q = $('findText').value; if (!q) return;
      const field = $('findField').value, cs = $('findCase').checked, rep = $('replaceText').value;
      const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), cs ? 'g' : 'gi');
      let n = 0;
      for (const c of state.project.cues) {
        const v = c[field] || ''; if (!re.test(v)) continue; re.lastIndex = 0;
        c[field] = v.replace(re, rep); n++;
        const row = rows.get(c.id); const ta = row.querySelector(field === 'text' ? '.orig' : '.trans'); ta.value = c[field]; autoGrow(ta);
        if (field === 'translation') { c.status = 'edited'; applyRowStatus(row, c); }
      }
      saveProject(); setStatus($('findMsg'), `已替换 ${n} 条`, 'ok');
    });

    $('reflowBtn').addEventListener('click', () => {
      const w = lineWidth();
      const sel = selectedCues();
      const list = sel.length ? sel : state.project.cues;
      // 按整个文件判断是否为双语（只整理选中的行时也一样）
      const bilingual = SubReflow.detectBilingual(state.project.cues.map(c => c.text));
      if (!confirm(`将把${sel.length ? `选中的 ${sel.length}` : `全部 ${list.length}`} 条字幕的原文和译文合并为一行，超过宽度上限（${w}，约 ${Math.floor(w / 2)} 个汉字）时再按断句换行。` +
        (bilingual ? '\n检测到原文是双语字幕：原文会按语言分成两行，中文一行、外文一行。' : '') + '\n继续？')) return;
      let n = 0;
      for (const c of list) {
        const t = bilingual ? SubReflow.reflowBilingual(c.text, w, bilingual) : SubReflow.reflow(c.text, w);
        const tr = c.translation ? SubReflow.reflow(c.translation, w) : c.translation;
        if (t !== c.text || tr !== c.translation) { c.text = t; c.translation = tr; refreshRowFull(c); n++; }
      }
      requestAnimationFrame(() => list.forEach(c => { const r = rows.get(c.id); if (r) r.querySelectorAll('textarea').forEach(autoGrow); }));
      saveProject(); toast(n ? `已整理 ${n} 条字幕${bilingual ? '（双语，已按语言分行）' : ''}` : '没有需要整理的字幕', 'ok');
    });
    $('swapBtn').addEventListener('click', () => {
      const n = state.project.cues.filter(c => c.translation.trim()).length;
      if (!n) { toast('没有译文可用'); return; }
      if (!confirm(`将把 ${n} 条译文设为原文，并清空译文栏（用于二次翻译/润色）。继续？`)) return;
      for (const c of state.project.cues) {
        if (!c.translation.trim()) continue;
        c.text = c.translation; c.translation = ''; c.status = ''; c.err = '';
        const row = rows.get(c.id); const o = row.querySelector('.orig'); o.value = c.text; autoGrow(o); refreshRow(c);
      }
      state.task.source_lang = state.task.target_lang; $('srcLang').value = state.task.source_lang; saveTask();
      updateCounter(); saveProject(); toast('已将译文设为原文', 'ok');
    });
    $('clearTransBtn').addEventListener('click', () => {
      if (!confirm('确定清空所有译文？')) return;
      for (const c of state.project.cues) { c.translation = ''; c.status = ''; c.err = ''; refreshRow(c); }
      updateCounter(); saveProject();
    });
    $('helpBtn').addEventListener('click', () => $('helpDlg').showModal());
  }

  // ---------- 账户 ----------
  function initAccountUI() {
    $('accountBtn').addEventListener('click', () => {
      ['accOld', 'accNew', 'accNew2'].forEach(id => $(id).value = '');
      $('accUser').value = state.username || '';
      setStatus($('accountMsg'), '');
      $('accountDlg').showModal(); $('accOld').focus();
    });
    $('accountCancel').addEventListener('click', () => $('accountDlg').close());
    $('logoutBtn').addEventListener('click', async () => {
      if (dirtyCues().length) await flushProject();
      try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (_) {}
      location.href = '/login.html';
    });
    $('accountSave').addEventListener('click', async () => {
      const old = $('accOld').value, nu = $('accUser').value.trim(), n1 = $('accNew').value, n2 = $('accNew2').value;
      if (!old) return setStatus($('accountMsg'), '请输入当前密码', 'err');
      if (!nu || /\s/.test(nu)) return setStatus($('accountMsg'), '用户名不能为空，也不能包含空格', 'err');
      if (n1.length < 6) return setStatus($('accountMsg'), '新密码至少 6 位', 'err');
      if (n1 !== n2) return setStatus($('accountMsg'), '两次输入的新密码不一致', 'err');
      $('accountSave').disabled = true; setStatus($('accountMsg'), '正在保存…');
      try {
        const res = await fetch('/api/auth/password', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ old_password: old, new_password: n1, new_username: nu }) });
        const j = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
        state.username = j.username; $('accountName').textContent = j.username;
        $('accountDlg').close(); toast('密码已修改，其他设备需要重新登录', 'ok');
      } catch (e) { setStatus($('accountMsg'), e.message, 'err'); }
      finally { $('accountSave').disabled = false; }
    });
  }

  // ---------- 启动 ----------
  async function init() {
    initSettingsUI(); initTaskUI(); initFileUI(); initExportUI(); initTools(); initAccountUI();
    renderProject();
    try {
      const st = await (await fetch('/api/auth/status')).json();
      if (!st.authenticated) return authLost();
      state.username = st.username; $('accountName').textContent = st.username;
    } catch (_) {}
    try { state.serverCfg = await (await api('/api/config')).json(); } catch (_) {}
    try { await loadServerSettings(); }
    catch (e) { toast('读取服务器设置失败：' + e.message, 'err'); }
    try { setSync('正在读取字幕…', 'busy'); await loadServerProject(); }
    catch (e) { setSync('⚠ 读取服务器上的字幕失败', 'err'); toast('读取服务器上的字幕失败：' + e.message, 'err'); }
    const onBack = () => { refreshServerSettings(); pullProject(); };
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') onBack(); });
    window.addEventListener('focus', onBack);
    // 每 5 秒拉取一次其他设备的修改（页面在前台时）
    setInterval(() => { if (document.visibilityState === 'visible') pullProject(); }, 5000);
    // 关闭页面前尽量把未保存的修改发出去
    window.addEventListener('beforeunload', (e) => {
      flushTask();
      if (!state.project || !dirtyCues().length) return;
      flushProject(true);
      e.preventDefault(); e.returnValue = '';
    });
    const a = activeProfile();
    if (a && !a.has_key && !['ollama', 'server'].includes(a.preset) && !state.serverCfg.server_has_key) {
      setTimeout(() => { toast('请先在「模型设置」中配置 API'); }, 600);
    }
  }
  document.addEventListener('DOMContentLoaded', init);
})();
