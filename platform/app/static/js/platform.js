/**
 * AQUA AI平台 - 主框架 v10.0
 * 深色主题 · SVG图标 · SPA路由 · 全中文体验
 * 全部图标使用纯SVG代码，不使用任何Emoji或Unicode字符
 */

/* ========== SVG 图标库（纯SVG，无Emoji） ========== */
const Icons = {
  // 品牌Logo
  logo: (size = 24) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#00d4ff"/><stop offset="1" stop-color="#448aff"/></linearGradient></defs><path d="M12 2L3 7v10l9 5 9-5V7l-9-5z" stroke="url(#lg)" stroke-width="1.5" fill="rgba(0,212,255,0.1)"/><path d="M8 14l4-8 4 8M9.5 11h5" stroke="url(#lg)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>`,

  // 导航/菜单
  overview: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>`,
  key: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4"/><path d="M10.85 12.15L19 4"/><path d="M18 5l2 2"/><path d="M15 8l2 2"/></svg>`,
  chat: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  models: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>`,
  stats: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`,
  docs: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`,
  settings: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,

  // 操作
  plus: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
  copy: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
  trash: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>`,
  edit: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`,
  eye: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`,
  eyeOff: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`,
  close: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  check: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  search: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
  send: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`,
  stop: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>`,
  refresh: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`,
  chevronLeft: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>`,
  chevronRight: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>`,
  chevronDown: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`,
  chevronUp: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>`,
  arrowRight: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`,

  // 状态
  info: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
  warn: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  error: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
  alert: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  success: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
  spinner: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" class="spin"><path d="M21 12a9 9 0 1 1-6.219-8.56" opacity="0.4"/><path d="M21 12a9 9 0 0 1-9 9"/></svg>`,

  // 主题
  sun: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`,
  moon: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
  menu: (s = 20) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>`,
  user: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  logout: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`,
  globe: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`,
  bolt: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
  shield: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  cpu: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>`,
  brain: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z"/></svg>`,
  database: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
  clock: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  upload: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
  link: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  terminal: (s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
  star: (s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
};

/* ========== API 封装 ========== */
const API = {
  async _request(method, url, data, options = {}) {
    const opts = {
      method,
      headers: {},
      credentials: 'same-origin',
      ...options,
    };
    if (data !== undefined && !(data instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(data);
    } else if (data instanceof FormData) {
      opts.body = data;
    }
    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      throw new Error('网络错误，请检查网络连接');
    }
    if (resp.status === 401) {
      // silent选项：用于初始fetchUser检查，不触发重定向
      if (!options.silent) {
        const hash = location.hash || '';
        if (!hash.startsWith('#/login') && !hash.startsWith('#/register')) {
          // 仅对受保护页面（/console开头）自动跳转登录，公开页不跳转
          if (hash.startsWith('#/console')) {
            location.hash = '#/login';
          }
        }
      }
      throw new Error('未登录或登录已过期');
    }
    let body = null;
    const text = await resp.text();
    if (text) {
      try { body = JSON.parse(text); } catch (_) { body = text; }
    }
    if (!resp.ok) {
      let msg = '请求失败';
      if (body && typeof body === 'object') {
        if (body.detail) {
          if (typeof body.detail === 'string') msg = body.detail;
          else if (body.detail.message) msg = body.detail.message;
          else msg = JSON.stringify(body.detail);
        } else if (body.error) {
          if (typeof body.error === 'string') msg = body.error;
          else if (body.error.message) msg = body.error.message;
        } else if (body.message) msg = body.message;
      } else if (typeof body === 'string') {
        msg = body;
      }
      throw new Error(msg);
    }
    return body;
  },
  get(url, opts) { return this._request('GET', url, undefined, opts); },
  post(url, data, opts) { return this._request('POST', url, data, opts); },
  put(url, data, opts) { return this._request('PUT', url, data, opts); },
  delete(url, opts) { return this._request('DELETE', url, undefined, opts); },
};

/* ========== 工具函数 ========== */
function escapeHtml(str) {
  if (str == null) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(str).replace(/[&<>"']/g, (c) => map[c]);
}

function escapeAttr(str) {
  if (str == null) return '';
  return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    Toast.show('已复制到剪贴板', 'success');
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      Toast.show('已复制到剪贴板', 'success');
    } catch (e) {
      Toast.show('复制失败', 'error');
    }
    document.body.removeChild(ta);
  }
}

function debounce(fn, ms) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

/* ========== 格式化函数 ========== */
const Fmt = {
  // 数字格式化：K/M
  number(n) {
    if (n == null || isNaN(n)) return '0';
    n = Number(n);
    if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(n);
  },
  // 完整数字（千分位）
  full(n) {
    if (n == null || isNaN(n)) return '0';
    return Number(n).toLocaleString('zh-CN');
  },
  // Token格式化（带颜色提示）
  token(n) {
    return this.number(n);
  },
  // 延迟格式化
  latency(ms) {
    if (ms == null || isNaN(ms)) return '-';
    ms = Number(ms);
    if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
    return Math.round(ms) + 'ms';
  },
  // 延迟颜色
  latencyColor(ms) {
    if (ms == null) return 'var(--text-muted)';
    ms = Number(ms);
    if (ms < 1000) return 'var(--accent-success)';
    if (ms < 3000) return 'var(--accent-warning)';
    return 'var(--accent-danger)';
  },
  // 时间相对
  timeAgo(dateStr) {
    if (!dateStr) return '-';
    try {
      const d = new Date(dateStr);
      const now = new Date();
      const diff = (now - d) / 1000;
      if (diff < 60) return '刚刚';
      if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
      if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
      if (diff < 2592000) return Math.floor(diff / 86400) + '天前';
      return d.toLocaleDateString('zh-CN');
    } catch (_) { return '-'; }
  },
  // 完整时间
  time(dateStr) {
    if (!dateStr) return '-';
    try {
      const d = new Date(dateStr);
      return d.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
      });
    } catch (_) { return '-'; }
  },
  // 状态码中文
  status(status) {
    if (!status) return { text: '未知', cls: 'muted' };
    if (status === 'success' || status === 200) return { text: '成功', cls: 'active' };
    if (status === 'error' || status >= 500) return { text: '服务端错误', cls: 'cooling' };
    if (status >= 400 && status < 500) return { text: '客户端错误', cls: 'softbusy' };
    return { text: String(status), cls: 'info' };
  },
  // 状态码
  httpStatus(code) {
    if (code == null) return { text: '-', cls: 'muted' };
    code = Number(code);
    if (code === 200) return { text: '成功', cls: 'active' };
    if (code === 429) return { text: '限流', cls: 'cooling' };
    if (code >= 500) return { text: '服务端错误', cls: 'cooling' };
    if (code >= 400) return { text: '客户端错误', cls: 'softbusy' };
    if (code >= 300) return { text: '重定向', cls: 'info' };
    return { text: String(code), cls: 'info' };
  },
  // 字节大小
  bytes(n) {
    if (n == null) return '-';
    n = Number(n);
    if (n < 1024) return n + 'B';
    if (n < 1048576) return (n / 1024).toFixed(1) + 'KB';
    if (n < 1073741824) return (n / 1048576).toFixed(1) + 'MB';
    return (n / 1073741824).toFixed(2) + 'GB';
  },
  // 百分比
  percent(n, total) {
    if (!total) return '0%';
    return ((n / total) * 100).toFixed(1) + '%';
  },
};

/* ========== Toast 通知系统 ========== */
const Toast = {
  _container: null,
  _init() {
    if (this._container) return;
    this._container = document.createElement('div');
    this._container.className = 'toast-container';
    document.body.appendChild(this._container);
  },
  show(msg, type = 'info', duration = 3000) {
    this._init();
    const item = document.createElement('div');
    item.className = `toast toast-${type}`;
    const iconMap = {
      success: Icons.success(16),
      error: Icons.error(16),
      warn: Icons.warn(16),
      info: Icons.info(16),
    };
    item.innerHTML = `<span class="toast-icon">${iconMap[type] || iconMap.info}</span><span class="toast-msg">${escapeHtml(msg)}</span>`;
    this._container.appendChild(item);
    requestAnimationFrame(() => item.classList.add('show'));
    setTimeout(() => {
      item.classList.remove('show');
      setTimeout(() => item.remove(), 300);
    }, duration);
  },
  success(msg, d) { this.show(msg, 'success', d); },
  error(msg, d) { this.show(msg, 'error', d || 4000); },
  warn(msg, d) { this.show(msg, 'warn', d); },
  info(msg, d) { this.show(msg, 'info', d); },
};

/* ========== 模态框管理 ========== */
const Modal = {
  _overlay: null,
  _title: null,
  _body: null,
  _footer: null,
  _onClose: null,
  _init() {
    if (this._overlay) return;
    this._overlay = document.createElement('div');
    this._overlay.className = 'modal-overlay';
    this._overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header">
          <h3 class="modal-title"></h3>
          <button class="modal-close" type="button">${Icons.close(18)}</button>
        </div>
        <div class="modal-body"></div>
        <div class="modal-footer"></div>
      </div>
    `;
    document.body.appendChild(this._overlay);
    this._title = this._overlay.querySelector('.modal-title');
    this._body = this._overlay.querySelector('.modal-body');
    this._footer = this._overlay.querySelector('.modal-footer');
    this._overlay.querySelector('.modal-close').onclick = () => this.close();
    this._overlay.onclick = (e) => {
      if (e.target === this._overlay) this.close();
    };
  },
  show(opts) {
    this._init();
    this._title.innerHTML = opts.title || '';
    this._body.innerHTML = opts.body || '';
    this._footer.innerHTML = opts.footer || '';
    this._onClose = opts.onClose || null;
    this._overlay.classList.add('show');
    this._overlay.style.pointerEvents = 'all';
    if (opts.onShow) opts.onShow(this._body, this._footer);
  },
  close() {
    if (!this._overlay) return;
    this._overlay.classList.remove('show');
    this._overlay.style.pointerEvents = 'none';
    if (this._onClose) {
      try { this._onClose(); } catch (_) {}
      this._onClose = null;
    }
  },
  confirm(opts) {
    return new Promise((resolve) => {
      this.show({
        title: opts.title || '确认',
        body: opts.body || '<p>确认执行此操作？</p>',
        footer: `
          <button class="btn" data-act="cancel">取消</button>
          <button class="btn ${opts.danger ? 'danger' : 'primary'}" data-act="ok">${opts.okText || '确认'}</button>
        `,
        onShow: (body, footer) => {
          footer.querySelector('[data-act="cancel"]').onclick = () => { this.close(); resolve(false); };
          footer.querySelector('[data-act="ok"]').onclick = () => { this.close(); resolve(true); };
        },
      });
    });
  },
};

// 暴露到window
window.openModal = (opts) => Modal.show(opts);
window.closeModal = () => Modal.close();

/* ========== 主题管理 ========== */
const Theme = {
  _key: 'aqua_theme',
  get() {
    return localStorage.getItem(this._key) || 'dark';
  },
  set(theme) {
    localStorage.setItem(this._key, theme);
    document.documentElement.setAttribute('data-theme', theme);
    this._updateToggle();
  },
  toggle() {
    const next = this.get() === 'dark' ? 'light' : 'dark';
    this.set(next);
  },
  _updateToggle() {
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.innerHTML = this.get() === 'dark' ? Icons.sun(18) : Icons.moon(18);
    }
  },
  init() {
    document.documentElement.setAttribute('data-theme', this.get());
    this._updateToggle();
  },
};

/* ========== 认证管理 ========== */
const Auth = {
  _user: null,
  async fetchUser() {
    try {
      // 使用silent选项，避免未登录时触发重定向（公开页也可访问）
      const data = await API.get('/api/user/profile', { silent: true });
      this._user = data;
      return data;
    } catch (e) {
      this._user = null;
      return null;
    }
  },
  getUser() { return this._user; },
  isLoggedIn() { return !!this._user; },
  clear() { this._user = null; },
  async logout() {
    try { await API.post('/api/auth/logout'); } catch (_) {}
    this.clear();
    location.hash = '#/';
  },
};

/* ========== 加载状态 ========== */
const Loader = {
  show(target, msg = '加载中') {
    if (typeof target === 'string') target = document.querySelector(target);
    if (!target) return;
    target.innerHTML = `
      <div class="loader">
        <div class="loader-spinner">${Icons.spinner(28)}</div>
        <div class="loader-text">${escapeHtml(msg)}</div>
      </div>
    `;
  },
  hide(target) {
    if (typeof target === 'string') target = document.querySelector(target);
  },
};

/* ========== 空状态 ========== */
function emptyState(msg, icon = 'info') {
  const iconFn = Icons[icon] || Icons.info;
  return `<div class="empty-state">
    <div class="empty-icon">${iconFn(36)}</div>
    <div class="empty-text">${escapeHtml(msg)}</div>
  </div>`;
}

/* ========== 分页器 ========== */
function renderPagination(container, total, page, pageSize, onChange) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  page = Math.max(1, Math.min(page, totalPages));
  if (!container) return;
  const parts = [];
  parts.push(`<div class="pagination">`);
  parts.push(`<div class="pagination-info">共 ${Fmt.full(total)} 条 / ${totalPages} 页</div>`);
  parts.push(`<div class="pagination-controls">`);
  parts.push(`<button class="btn sm" data-page="1" ${page === 1 ? 'disabled' : ''}>首页</button>`);
  parts.push(`<button class="btn sm" data-page="${page - 1}" ${page === 1 ? 'disabled' : ''}>${Icons.chevronLeft(14)}</button>`);
  // 页码
  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, page + 2);
  if (start > 1) parts.push(`<span class="page-ellipsis">...</span>`);
  for (let i = start; i <= end; i++) {
    parts.push(`<button class="btn sm ${i === page ? 'primary' : ''}" data-page="${i}">${i}</button>`);
  }
  if (end < totalPages) parts.push(`<span class="page-ellipsis">...</span>`);
  parts.push(`<button class="btn sm" data-page="${page + 1}" ${page === totalPages ? 'disabled' : ''}>${Icons.chevronRight(14)}</button>`);
  parts.push(`<button class="btn sm" data-page="${totalPages}" ${page === totalPages ? 'disabled' : ''}>末页</button>`);
  parts.push(`</div></div>`);
  container.innerHTML = parts.join('');
  container.querySelectorAll('[data-page]').forEach(btn => {
    btn.onclick = () => {
      const p = parseInt(btn.dataset.page, 10);
      if (!isNaN(p) && p !== page) onChange(p);
    };
  });
}

/* ========== 表格排序 ========== */
function applySort(data, field, order) {
  return [...data].sort((a, b) => {
    let va = a[field], vb = b[field];
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va < vb) return order === 'asc' ? -1 : 1;
    if (va > vb) return order === 'asc' ? 1 : -1;
    return 0;
  });
}

/* ========== SPA 路由 ========== */
const Router = {
  routes: {},
  current: null,
  register(hash, handler) {
    this.routes[hash] = handler;
  },
  async handle() {
    // 移动端路由切换时自动关闭侧边栏
    if (window.innerWidth <= 768) {
      const sidebar = document.getElementById('app-sidebar');
      const overlay = document.getElementById('sidebar-overlay');
      if (sidebar) { sidebar.classList.remove('mobile-open'); }
      if (overlay) { overlay.classList.remove('active'); }
    }
    const hash = location.hash.slice(1) || '/';
    // 解析路径与参数
    const [path, queryStr] = hash.split('?');
    const query = {};
    if (queryStr) {
      queryStr.split('&').forEach(pair => {
        const [k, v] = pair.split('=');
        query[decodeURIComponent(k)] = decodeURIComponent(v || '');
      });
    }
    // 精确匹配
    let handler = this.routes[path];
    // 动态匹配（支持 :param）
    if (!handler) {
      for (const route of Object.keys(this.routes)) {
        if (route.includes(':')) {
          const pattern = route.replace(/:[^/]+/g, '([^/]+)');
          const re = new RegExp('^' + pattern + '$');
          const m = path.match(re);
          if (m) {
            handler = this.routes[route];
            const paramNames = (route.match(/:[^/]+/g) || []).map(p => p.slice(1));
            const params = {};
            paramNames.forEach((n, i) => params[n] = m[i]);
            this.current = { path, query, params };
            await this._invoke(handler, params, query);
            return;
          }
        }
      }
    }
    this.current = { path, query, params: {} };
    if (handler) {
      await this._invoke(handler, {}, query);
    } else {
      // 默认跳转首页
      location.hash = '#/';
    }
  },
  async _invoke(handler, params, query) {
    try {
      await handler(params, query);
    } catch (e) {
      console.error('路由执行错误:', e);
      Toast.error('页面加载失败：' + e.message);
    }
    // 滚动到顶部
    const content = document.getElementById('content-area');
    if (content) content.scrollTop = 0;
  },
  go(path) {
    location.hash = '#' + path;
  },
  init() {
    window.addEventListener('hashchange', () => this.handle());
    this.handle();
  },
};

/* ========== 全局错误处理 ========== */
window.addEventListener('unhandledrejection', (e) => {
  console.error('未捕获Promise错误:', e.reason);
  if (e.reason && e.reason.message && !e.reason.message.includes('未登录')) {
    Toast.error(e.reason.message, 4000);
  }
});

/* ========== 启动初始化 ========== */
document.addEventListener('DOMContentLoaded', () => {
  Theme.init();
});

// 导出全局（确保HTML中可用）
window.Icons = Icons;
window.API = API;
window.Fmt = Fmt;
window.Toast = Toast;
window.Modal = Modal;
window.Theme = Theme;
window.Auth = Auth;
window.Loader = Loader;
window.Router = Router;
window.escapeHtml = escapeHtml;
window.escapeAttr = escapeAttr;
window.copyToClipboard = copyToClipboard;
window.emptyState = emptyState;
window.renderPagination = renderPagination;
window.applySort = applySort;
window.debounce = debounce;
