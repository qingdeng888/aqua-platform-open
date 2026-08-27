// 职责：UI 基础设施——XSS 转义、Toast、Modal、Loader/骨架屏、空态(含重试)、复制反馈、格式化、分页、排序、SVG 图表

import { Icons } from './icons.js';

/* ===== XSS 转义工具 ===== */
export function escapeHtml(str) {
  if (str == null) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(str).replace(/[&<>"']/g, (c) => map[c]);
}

export function escapeAttr(str) {
  if (str == null) return '';
  return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** URL 渲染安全化：拦截 javascript:/vbscript:/data: 等危险协议 */
export function safeUrl(url) {
  if (url == null) return '#';
  const s = String(url).trim();
  if (/^(https?:|mailto:|#|\/)/i.test(s)) return s;
  return '#';
}

/** 供模板使用的简短别名 */
export const esc = escapeHtml;

/* ===== 剪贴板（带按钮成功反馈） ===== */
const secretRegistry = new Map();
let secretSeq = 0;

/** 登记敏感文本（如完整密钥），返回引用 id，避免把它拼进 HTML 属性 */
export function registerSecret(value) {
  const id = 'sec-' + (++secretSeq) + '-' + Math.random().toString(36).slice(2, 8);
  secretRegistry.set(id, value);
  return id;
}

export function getSecret(id) { return secretRegistry.get(id) || ''; }

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
}

/** 复制文本并给出 toast + 按钮图标切换为对勾的反馈；btn 为触发按钮（可选） */
export async function copyToClipboard(text, successMsg = '已复制到剪贴板', btn = null) {
  const ok = await writeClipboard(text);
  if (ok) {
    Toast.show(successMsg, 'success');
    if (btn) flashCopied(btn);
  } else {
    Toast.show('复制失败', 'error');
  }
}

function flashCopied(btn) {
  if (!btn || btn.dataset.copying === '1') return;
  btn.dataset.copying = '1';
  const original = btn.innerHTML;
  btn.classList.add('copied');
  btn.innerHTML = Icons.check(14);
  setTimeout(() => {
    btn.innerHTML = original;
    btn.classList.remove('copied');
    delete btn.dataset.copying;
  }, 1500);
}

/**
 * 全局复制事件委托：
 *  - data-copy="<静态字符串>"      复制字面量（URL、模型ID 等）
 *  - data-copy-target="<元素id>"   复制元素 textContent（代码块）
 *  - data-copy-ref="<注册id>"      复制 registerSecret 登记的敏感值
 * 页面模块只需渲染按钮，无需为动态值拼内联事件。
 */
export function bindCopyDelegation() {
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-copy],[data-copy-target],[data-copy-ref]');
    if (!btn) return;
    let text = '';
    let msg = '已复制到剪贴板';
    if (btn.dataset.copy !== undefined) {
      text = btn.dataset.copy;
      msg = btn.dataset.copyMsg || msg;
    } else if (btn.dataset.copyTarget) {
      const el = document.getElementById(btn.dataset.copyTarget);
      if (el) text = el.textContent;
    } else if (btn.dataset.copyRef) {
      text = getSecret(btn.dataset.copyRef);
      msg = btn.dataset.copyMsg || msg;
    }
    if (!text) return;
    await copyToClipboard(text, msg, btn);
  });
}

/* ===== Toast 通知 ===== */
export const Toast = {
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
    const iconEl = document.createElement('span');
    iconEl.className = 'toast-icon';
    iconEl.innerHTML = iconMap[type] || iconMap.info;
    const msgEl = document.createElement('span');
    msgEl.className = 'toast-msg';
    msgEl.textContent = msg; // 消息一律 textContent，防注入
    item.appendChild(iconEl);
    item.appendChild(msgEl);
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

/* ===== 模态框 ===== */
export const Modal = {
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
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-header">
          <h3 class="modal-title"></h3>
          <button class="modal-close" type="button" aria-label="关闭">${Icons.close(18)}</button>
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
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this._overlay && this._overlay.classList.contains('show')) this.close();
    });
  },
  /** opts: { title, body, footer, onShow(body, footer), onClose, large } */
  show(opts) {
    this._init();
    this._title.innerHTML = opts.title || '';
    this._body.innerHTML = opts.body || '';
    this._footer.innerHTML = opts.footer || '';
    this._footer.style.display = opts.footer ? '' : 'none';
    this._overlay.querySelector('.modal').classList.toggle('modal-lg', !!opts.large);
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
  /** 确认框，返回 Promise<boolean> */
  confirm(opts) {
    return new Promise((resolve) => {
      this.show({
        title: opts.title || '确认',
        body: opts.body || '<p>确认执行此操作？</p>',
        footer: `
          <button class="btn" data-act="cancel">取消</button>
          <button class="btn ${opts.danger ? 'danger' : 'primary'}" data-act="ok">${escapeHtml(opts.okText || '确认')}</button>
        `,
        onShow: (body, footer) => {
          footer.querySelector('[data-act="cancel"]').onclick = () => { this.close(); resolve(false); };
          footer.querySelector('[data-act="ok"]').onclick = () => { this.close(); resolve(true); };
        },
      });
    });
  },
};

/* ===== 加载状态与骨架屏 ===== */
export const Loader = {
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
  skeleton(target, rows = 3) {
    if (typeof target === 'string') target = document.querySelector(target);
    if (!target) return;
    let html = '<div class="skeleton">';
    for (let i = 0; i < rows; i++) {
      html += `<div class="skeleton-line ${i % 3 === 2 ? 'w-60' : ''}"></div>`;
    }
    target.innerHTML = html + '</div>';
  },
};

/* ===== 空状态 ===== */
export function emptyState(msg, icon = 'info') {
  const iconFn = Icons[icon] || Icons.info;
  return `<div class="empty-state">
    <div class="empty-icon">${iconFn(36)}</div>
    <div class="empty-text">${escapeHtml(msg)}</div>
  </div>`;
}

/** 失败态：错误信息 + 重试按钮（请求失败统一出口） */
export function errorState(msg, retryFn, icon = 'error') {
  const wrap = document.createElement('div');
  wrap.className = 'empty-state';
  const iconEl = document.createElement('div');
  iconEl.className = 'empty-icon';
  iconEl.innerHTML = (Icons[icon] || Icons.error)(36);
  const textEl = document.createElement('div');
  textEl.className = 'empty-text';
  textEl.textContent = msg;
  const btn = document.createElement('button');
  btn.className = 'btn sm';
  btn.innerHTML = Icons.refresh(14) + ' 重试';
  btn.onclick = () => { if (typeof retryFn === 'function') retryFn(); };
  wrap.appendChild(iconEl);
  wrap.appendChild(textEl);
  wrap.appendChild(btn);
  return wrap;
}

/* ===== 分页器 ===== */
export function renderPagination(container, total, page, pageSize, onChange) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  page = Math.max(1, Math.min(page, totalPages));
  if (!container) return;
  const parts = [];
  parts.push('<div class="pagination">');
  parts.push(`<div class="pagination-info">共 ${Fmt.full(total)} 条 / ${totalPages} 页</div>`);
  parts.push('<div class="pagination-controls">');
  parts.push(`<button class="btn sm" data-page="1" ${page === 1 ? 'disabled' : ''}>首页</button>`);
  parts.push(`<button class="btn sm" data-page="${page - 1}" ${page === 1 ? 'disabled' : ''}>${Icons.chevronLeft(14)}</button>`);
  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, page + 2);
  if (start > 1) parts.push('<span class="page-ellipsis">...</span>');
  for (let i = start; i <= end; i++) {
    parts.push(`<button class="btn sm ${i === page ? 'primary' : ''}" data-page="${i}">${i}</button>`);
  }
  if (end < totalPages) parts.push('<span class="page-ellipsis">...</span>');
  parts.push(`<button class="btn sm" data-page="${page + 1}" ${page === totalPages ? 'disabled' : ''}>${Icons.chevronRight(14)}</button>`);
  parts.push(`<button class="btn sm" data-page="${totalPages}" ${page === totalPages ? 'disabled' : ''}>末页</button>`);
  parts.push('</div></div>');
  container.innerHTML = parts.join('');
  container.querySelectorAll('[data-page]').forEach((btn) => {
    btn.onclick = () => {
      const p = parseInt(btn.dataset.page, 10);
      if (!isNaN(p) && p !== page) onChange(p);
    };
  });
}

/* ===== 表格排序 ===== */
export function applySort(data, field, order) {
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

/* ===== 工具 ===== */
export function debounce(fn, ms) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

/** 密码输入框右侧小眼睛切换 */
export function bindPasswordToggle(root = document) {
  root.querySelectorAll('[data-toggle]').forEach((btn) => {
    btn.onclick = () => {
      const input = document.getElementById(btn.dataset.toggle);
      if (!input) return;
      if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = Icons.eyeOff(16);
      } else {
        input.type = 'password';
        btn.innerHTML = Icons.eye(16);
      }
    };
  });
}

/* ===== 格式化 ===== */
export const Fmt = {
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
  // 相对时间
  timeAgo(dateStr) {
    if (!dateStr) return '-';
    try {
      const d = new Date(dateStr);
      const diff = (Date.now() - d) / 1000;
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
      return new Date(dateStr).toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
      });
    } catch (_) { return '-'; }
  },
  // 请求状态（字符串或数字）中文
  status(status) {
    if (!status) return { text: '未知', cls: 'muted' };
    if (status === 'success' || status === 200) return { text: '成功', cls: 'active' };
    if (status === 'error' || status >= 500) return { text: '服务端错误', cls: 'cooling' };
    if (status >= 400 && status < 500) return { text: '客户端错误', cls: 'softbusy' };
    return { text: String(status), cls: 'info' };
  },
  // 百分比
  percent(n, total) {
    if (!total) return '0%';
    return ((n / total) * 100).toFixed(1) + '%';
  },
};

/* ===== 手写 SVG 图表（sparkline 折线 + 面积） ===== */
export const Charts = {
  /**
   * 7日趋势 sparkline：values 为 {label, value, tip} 数组
   * 返回 SVG 字符串（纯手写，无第三方库）
   */
  sparkline(items, opts = {}) {
    const w = opts.width || 560;
    const h = opts.height || 200;
    const padX = 28, padTop = 18, padBottom = 26;
    const innerW = w - padX * 2;
    const innerH = h - padTop - padBottom;
    if (!items || !items.length) {
      return `<div class="empty-state" style="padding:24px"><div class="empty-text">暂无趋势数据</div></div>`;
    }
    const max = Math.max(...items.map((d) => d.value || 0), 1);
    const step = items.length > 1 ? innerW / (items.length - 1) : 0;
    const pts = items.map((d, i) => {
      const x = padX + step * i;
      const y = padTop + innerH - ((d.value || 0) / max) * innerH;
      return { x, y, ...d };
    });

    const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
    const area = path + ` L ${pts[pts.length - 1].x.toFixed(1)} ${padTop + innerH} L ${pts[0].x.toFixed(1)} ${padTop + innerH} Z`;

    const gridLines = [0.25, 0.5, 0.75].map((f) => {
      const y = padTop + innerH - innerH * f;
      return `<line class="grid-line" x1="${padX}" y1="${y.toFixed(1)}" x2="${w - padX}" y2="${y.toFixed(1)}" stroke-dasharray="3 4"/>`;
    }).join('');

    const dots = pts.map((p) => {
      const tip = escapeAttr(p.tip || `${p.label}: ${p.value}`);
      return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3.5" fill="var(--bg-primary)" stroke="var(--accent-primary)" stroke-width="2"><title>${tip}</title></circle>`;
    }).join('');

    const labels = pts.map((p, i) => {
      if (pts.length > 8 && i % 2 === 1) return '';
      return `<text class="axis-label" x="${p.x.toFixed(1)}" y="${h - 8}" text-anchor="middle">${escapeHtml(p.label || '')}</text>`;
    }).join('');

    const values = pts.map((p, i) => {
      if (pts.length > 8 && i % 2 === 1) return '';
      return `<text class="axis-label" x="${p.x.toFixed(1)}" y="${(p.y - 8).toFixed(1)}" text-anchor="middle" fill="var(--text-secondary)">${escapeHtml(String(p.value))}</text>`;
    }).join('');

    return `<div class="chart-wrap"><svg class="chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img">
      ${gridLines}
      <defs><linearGradient id="spark-area" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="rgba(0,212,255,0.28)"/><stop offset="1" stop-color="rgba(0,212,255,0.02)"/>
      </linearGradient></defs>
      <path d="${area}" fill="url(#spark-area)"/>
      <path d="${path}" fill="none" stroke="var(--accent-primary)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${dots}${values}${labels}
    </svg></div>`;
  },
};

window.escapeHtml = escapeHtml;
window.escapeAttr = escapeAttr;
window.copyToClipboard = copyToClipboard;
window.Toast = Toast;
window.Modal = Modal;
window.Fmt = Fmt;
window.Loader = Loader;
