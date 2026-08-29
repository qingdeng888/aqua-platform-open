/* AQUA Gateway 管理控制台 — core.js
 *
 * 职责：全局状态、统一 API（Bearer token，401 自动回登录）、导航骨架（侧栏/移动
 * tabs/面包屑/active 态）、通用组件（toast/modal/confirm/表单弹窗/badge/统计卡/分页/
 * 加载·空·错误态）、全局事件委托。
 *
 * XSS 纪律：
 *  - 动态数据一律 esc() 或 textContent 输出；禁止数据进内联事件属性；
 *  - 行内按钮只带 data-act / data-id 等索引类属性，实际数据从缓存按索引读取；
 *  - 密钥/令牌明文仅在管理员主动点击 reveal/创建动作后展示，值经 .value 赋值而非属性内插。
 */
(function () {
'use strict';

var A = '/gw/admin';

var PN = ['dash', 'keys', 'proxies', 'clients', 'buckets', 'logs', 'algo', 'monitor', 'config', 'errors', 'commercial', 'mtest'];
var PT = {
  dash: '仪表盘', keys: '上游密钥', proxies: '代理池', clients: '下游客户', buckets: '桶监控',
  logs: '请求日志', algo: '算法引擎', monitor: '系统监控', config: '系统配置',
  errors: '错误码', commercial: '商用检测', mtest: '模型测试',
};
var ALGO_SUB = [
  { id: 'algo1', name: '滑动窗口' },
  { id: 'algo2', name: '软繁忙标记器' },
  { id: 'algo3', name: '自适应阈值' },
  { id: 'algo4', name: '冷却时长计算' },
  { id: 'algo5', name: '客户端并发' },
  { id: 'algo6', name: '客户端突发率' },
  { id: 'algo7', name: '日用量' },
  { id: 'algo8', name: '5xx退避' },
  { id: 'algo9', name: '区域故障隔离' },
  { id: 'algo10', name: '全局健康度' },
  { id: 'algo11', name: '池化权重' },
  { id: 'algo12', name: '自适应负载' },
  { id: 'algo13', name: '冷密钥预热' },
  { id: 'algo14', name: '异常自愈' },
  { id: 'algo15', name: '趋势感知均衡(Trae)' },
  { id: 'algo16', name: '龙虾调度(Lobster)' },
  { id: 'algo17', name: '严格公平调度' },
];

// 全局状态（token 沿用 localStorage 't' 键，保持旧会话兼容）
var S = {
  token: localStorage.getItem('t') || '',
  pg: 'dash',
  logFilters: { status_code: '', http_method: '', model: '', request_path: '', client_ip: '' }, // 日志筛选状态保持
};
var R = {};       // 页面渲染器注册表
var actions = {}; // 全局动作注册表（事件委托分发）
var cache = {};   // 列表缓存（事件委托按 data-idx 取整行数据）

var GW = window.GW = { A: A, S: S, R: R, actions: actions, cache: cache, PN: PN, PT: PT, ALGO_SUB: ALGO_SUB };

function $(id) { return document.getElementById(id); }
GW.$ = $;

/* ================= 工具 ================= */
function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
GW.esc = esc;

function fmtLatency(ms) {
  if (ms === null || ms === undefined || ms === '') return '-';
  ms = Number(ms);
  if (isNaN(ms)) return '-';
  if (ms >= 60000) return (ms / 60000).toFixed(1) + 'm';
  if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
  return ms + 'ms';
}
GW.fmtLatency = fmtLatency;

/* 时间渲染：后端统一写 UTC Z 格式，这里转成浏览器本地时区展示。
   withSec=true 时带秒（详情弹窗用）。无法解析的值退化为原字符串截断。 */
function fmtTime(t, withSec) {
  if (!t) return '-';
  var d = new Date(String(t));
  if (isNaN(d.getTime())) return String(t).slice(0, withSec ? 19 : 16).replace('T', ' ');
  var p = function (n) { return n < 10 ? '0' + n : String(n); };
  var s = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
    ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  return withSec ? s + ':' + p(d.getSeconds()) : s;
}
GW.fmtTime = fmtTime;

function fmtNum(n) {
  if (n === null || n === undefined || n === '') return '-';
  n = Number(n);
  if (isNaN(n)) return String(n);
  return n.toLocaleString('en-US');
}
GW.fmtNum = fmtNum;

/* ================= Toast ================= */
function toast(msg, type) {
  var el = document.createElement('div');
  el.className = 'toast toast-' + (type || 'info');
  el.textContent = msg;
  $('toastContainer').appendChild(el);
  setTimeout(function () {
    el.style.animation = 'toastOut .25s ease forwards';
    setTimeout(function () { el.remove(); }, 250);
  }, 3000);
}
GW.toast = toast;

/* ================= API（契约不变：/gw/admin + Bearer token） ================= */
async function api(path, options) {
  options = options || {};
  var headers = Object.assign({}, options.headers || {});
  headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  var resp = await fetch(A + path, Object.assign({}, options, { headers: headers }));
  if (resp.status === 401) {
    S.token = '';
    localStorage.removeItem('t');
    // 延迟渲染登录页：避免先于调用方 catch 块执行，导致 errorCard 覆盖登录页
    setTimeout(render, 0);
    throw new Error('登录已过期，请重新登录');
  }
  var data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  if (!resp.ok) {
    var d = data && data.detail ? data.detail : data;
    var msg = (d && d.message) || (data && data.message) || ('请求失败 (' + resp.status + ')');
    throw new Error(msg);
  }
  return data;
}
GW.api = api;

/* ================= 弹窗 ================= */
function showModal(html) {
  $('modalContent').innerHTML = html;
  $('modalOverlay').classList.add('show');
}
GW.showModal = showModal;

function closeModal() {
  $('modalOverlay').classList.remove('show');
}
GW.closeModal = closeModal;

// 通用二次确认（title/body 经 textContent 写入，杜绝注入）
function confirmModal(o) {
  var box = $('modalContent');
  box.textContent = '';
  var head = document.createElement('div'); head.className = 'modal-header';
  var h3 = document.createElement('h3'); h3.textContent = o.title || '确认操作';
  var x = document.createElement('button'); x.className = 'modal-close'; x.textContent = '×'; x.dataset.act = 'dismiss-modal';
  head.appendChild(h3); head.appendChild(x);

  var body = document.createElement('div'); body.className = 'modal-body';
  var p = document.createElement('p'); p.textContent = o.body || '确定要执行此操作吗？';
  body.appendChild(p);

  var foot = document.createElement('div'); foot.className = 'modal-footer';
  var cancel = document.createElement('button'); cancel.className = 'btn'; cancel.textContent = '取消'; cancel.dataset.act = 'dismiss-modal';
  var ok = document.createElement('button');
  ok.className = 'btn ' + (o.danger ? 'btn-danger' : 'btn-primary');
  ok.textContent = o.confirmText || (o.danger ? '确定删除' : '确定');
  foot.appendChild(cancel); foot.appendChild(ok);

  box.appendChild(head); box.appendChild(body); box.appendChild(foot);
  $('modalOverlay').classList.add('show');
  ok.addEventListener('click', async function () {
    closeModal();
    if (o.onConfirm) { try { await o.onConfirm(); } catch (e) { toast(e.message || '操作失败', 'error'); } }
  });
}
GW.confirmModal = confirmModal;

// 通用表单弹窗：字段值经 .value 赋值（不内插属性）；onSubmit 抛错则弹窗保留
// fields: [{id,label,type(text|number|password|select|scopes),value,placeholder,options:[{value,label,selected|checked}],step}]
function formModal(o) {
  var box = $('modalContent');
  box.textContent = '';
  var head = document.createElement('div'); head.className = 'modal-header';
  var h3 = document.createElement('h3'); h3.textContent = o.title || '表单';
  var x = document.createElement('button'); x.className = 'modal-close'; x.textContent = '×'; x.dataset.act = 'dismiss-modal';
  head.appendChild(h3); head.appendChild(x);

  var body = document.createElement('div'); body.className = 'modal-body';
  var inputs = {};
  (o.fields || []).forEach(function (f) {
    var g = document.createElement('div'); g.className = 'form-group';
    var lab = document.createElement('label'); lab.textContent = f.label; g.appendChild(lab);

    if (f.type === 'select') {
      var sel = document.createElement('select');
      (f.options || []).forEach(function (op) {
        var opt = document.createElement('option');
        opt.value = op.value;
        opt.textContent = op.label;
        if (op.selected) opt.selected = true;
        sel.appendChild(opt);
      });
      g.appendChild(sel);
      inputs[f.id] = { kind: 'select', el: sel };
    } else {
      var inp = document.createElement('input');
      inp.type = f.type || 'text';
      if (f.placeholder) inp.placeholder = f.placeholder;
      if (f.step) inp.step = f.step;
      if (f.value !== undefined && f.value !== null) inp.value = f.value;
      g.appendChild(inp);
      inputs[f.id] = { kind: 'input', el: inp };
    }
    body.appendChild(g);
  });
  if (o.hint) {
    var hint = document.createElement('p'); hint.className = 'form-hint'; hint.textContent = o.hint;
    body.appendChild(hint);
  }

  var foot = document.createElement('div'); foot.className = 'modal-footer';
  var cancel = document.createElement('button'); cancel.className = 'btn'; cancel.textContent = '取消'; cancel.dataset.act = 'dismiss-modal';
  var submit = document.createElement('button');
  submit.className = 'btn ' + (o.danger ? 'btn-danger' : 'btn-primary');
  submit.textContent = o.submitText || '保存';
  foot.appendChild(cancel); foot.appendChild(submit);

  box.appendChild(head); box.appendChild(body); box.appendChild(foot);
  $('modalOverlay').classList.add('show');

  submit.addEventListener('click', async function () {
    var vals = {};
    Object.keys(inputs).forEach(function (k) {
      var it = inputs[k];
      vals[k] = it.el.value.trim();
    });
    submit.disabled = true;
    try {
      // onSubmit 返回 false 表示自行接管弹窗（如创建成功后改为展示一次性明文），此时不再自动关闭
      var res = await o.onSubmit(vals);
      if (res !== false) closeModal();
    } catch (e) {
      toast(e.message || '操作失败', 'error');
    } finally {
      submit.disabled = false;
    }
  });
}
GW.formModal = formModal;

// 密钥/令牌明文展示弹窗：值经 .value 赋值，复制按钮监听器绑定（明文只在管理员主动动作后出现）
function secretModal(o) {
  var box = $('modalContent');
  box.textContent = '';
  var head = document.createElement('div'); head.className = 'modal-header';
  var h3 = document.createElement('h3'); h3.textContent = o.title || '密钥明文';
  var x = document.createElement('button'); x.className = 'modal-close'; x.textContent = '×'; x.dataset.act = 'dismiss-modal';
  head.appendChild(h3); head.appendChild(x);

  var body = document.createElement('div'); body.className = 'modal-body';
  if (o.note) { var n = document.createElement('p'); n.className = 'mb-8'; n.textContent = o.note; body.appendChild(n); }
  var inp = document.createElement('input'); inp.type = 'text'; inp.className = 'secret-input'; inp.readOnly = true;
  inp.value = o.secret || '';
  body.appendChild(inp);
  if (o.warn) { var w = document.createElement('p'); w.className = 'warn mt-12'; w.textContent = o.warn; body.appendChild(w); }

  var foot = document.createElement('div'); foot.className = 'modal-footer';
  var close = document.createElement('button'); close.className = 'btn'; close.textContent = '关闭'; close.dataset.act = 'dismiss-modal';
  var copy = document.createElement('button'); copy.className = 'btn btn-primary'; copy.textContent = '复制';
  foot.appendChild(close); foot.appendChild(copy);

  box.appendChild(head); box.appendChild(body); box.appendChild(foot);
  $('modalOverlay').classList.add('show');
  copy.addEventListener('click', function () {
    navigator.clipboard.writeText(o.secret || '').then(function () { toast('已复制到剪贴板', 'success'); }, function () { toast('复制失败，请手动选择复制', 'error'); });
  });
}
GW.secretModal = secretModal;

/* ================= 组件 ================= */
function spinner() { return '<div class="spinner"></div>'; }
GW.spinner = spinner;

function emptyState(msg, ico) {
  return '<div class="state-block card"><span class="ico">' + (ico || '📦') + '</span><div class="msg">' + esc(msg) + '</div></div>';
}
GW.emptyState = emptyState;

function errorCard(msg) {
  return '<div class="state-block card"><span class="ico">⚠</span><div class="msg">加载失败: ' + esc(msg) + '</div>' +
    '<button class="btn btn-sm" data-act="reload-page">重试</button></div>';
}
GW.errorCard = errorCard;

function badge(text, color) {
  return '<span class="badge badge-' + (color || 'gray') + '">' + esc(text) + '</span>';
}
GW.badge = badge;

// items: [{label, value(可为数值/字符串，或 {html:true} 时的可信内部 HTML), sub}]
function statGrid(items) {
  var grid = document.createElement('div');
  grid.className = 'stat-grid';
  items.forEach(function (it) {
    var card = document.createElement('div');
    card.className = 'stat-card' + (it.click ? ' clickable' : '');
    var lab = document.createElement('div'); lab.className = 'stat-label'; lab.textContent = it.label;
    var val = document.createElement('div'); val.className = 'stat-value';
    if (it.value && it.value.html) val.innerHTML = it.value.html; else val.textContent = (it.value === undefined || it.value === null || it.value === '') ? '-' : String(it.value);
    card.appendChild(lab); card.appendChild(val);
    if (it.sub) { var s = document.createElement('div'); s.className = 'stat-sub'; s.textContent = it.sub; card.appendChild(s); }
    if (it.click) card.addEventListener('click', it.click);
    grid.appendChild(card);
  });
  return grid;
}
GW.statGrid = statGrid;

function pagination(page, total, totalPages, cb) {
  var p = document.createElement('div');
  p.className = 'pagination';
  var prev = document.createElement('button');
  prev.className = 'btn btn-sm'; prev.textContent = '上一页'; prev.disabled = page <= 1;
  var info = document.createElement('span');
  info.className = 'page-info'; info.textContent = '第 ' + page + '/' + totalPages + ' 页 (共 ' + total + ' 条)';
  var next = document.createElement('button');
  next.className = 'btn btn-sm'; next.textContent = '下一页'; next.disabled = page >= totalPages;
  p.appendChild(prev); p.appendChild(info); p.appendChild(next);
  var go = function (np) { if (np >= 1 && np <= totalPages && np !== page) cb(np); };
  prev.addEventListener('click', function () { go(page - 1); });
  next.addEventListener('click', function () { go(page + 1); });
  return p;
}
GW.pagination = pagination;

function headerActions(html) { $('headerActions').innerHTML = html || ''; }
GW.headerActions = headerActions;

/* ================= 导航骨架 ================= */
function navTo(p) {
  if (!p) p = 'dash';
  if (PN.indexOf(p) === -1 && !ALGO_SUB.some(function (s) { return s.id === p; })) p = 'dash';
  S.pg = p;
  render();
}
GW.navTo = navTo;

function renderNav() {
  var sidebar = $('sidebarNav');
  var mobile = $('mobileTabs');
  sidebar.textContent = '';
  mobile.textContent = '';
  if (!S.token) return; // 未登录不渲染导航

  PN.forEach(function (p) {
    var n = document.createElement('div');
    n.className = 'nav-item' + (p === S.pg ? ' active' : '');
    n.dataset.act = 'nav';
    n.dataset.page = p;
    n.textContent = PT[p];
    sidebar.appendChild(n);
    if (p === 'algo') {
      ALGO_SUB.forEach(function (sub) {
        var sn = document.createElement('div');
        sn.className = 'nav-item nav-sub' + (S.pg === sub.id ? ' active' : '');
        sn.dataset.act = 'nav';
        sn.dataset.page = sub.id;
        sn.textContent = sub.name;
        sidebar.appendChild(sn);
      });
    }
    var mt = document.createElement('div');
    mt.className = 'tab-item' + (p === S.pg ? ' active' : '');
    mt.dataset.act = 'nav';
    mt.dataset.page = p;
    mt.textContent = PT[p];
    mobile.appendChild(mt);
  });
}

function renderCrumb() {
  var crumb = $('crumb');
  crumb.textContent = '';
  var root = document.createElement('span'); root.textContent = '网关管理';
  var sep = document.createElement('span'); sep.className = 'sep'; sep.textContent = '/';
  var cur = document.createElement('span'); cur.className = 'cur';
  var sub = ALGO_SUB.some(function (s) { return s.id === S.pg; }) ? ALGO_SUB.filter(function (s) { return s.id === S.pg; })[0] : null;
  if (!S.token) {
    cur.textContent = '登录';
  } else if (sub) {
    var a = document.createElement('span'); a.textContent = '算法引擎';
    var sep2 = document.createElement('span'); sep2.className = 'sep'; sep2.textContent = '/';
    crumb.appendChild(root); crumb.appendChild(sep); crumb.appendChild(a); crumb.appendChild(sep2); crumb.appendChild(cur);
    cur.textContent = sub.name;
    return;
  } else {
    cur.textContent = PT[S.pg] || '仪表盘';
  }
  crumb.appendChild(root); crumb.appendChild(sep); crumb.appendChild(cur);
}

function closeSidebar() {
  $('sidebar').classList.remove('open');
  $('overlay').classList.remove('show');
}

/* ================= 渲染入口 ================= */
function render() {
  cache = {}; GW.cache = cache; // 页面切换时清空列表缓存
  closeModal();
  renderNav();
  renderCrumb();
  var c = $('content');
  c.innerHTML = '';
  if (!S.token) { R.login(); return; }
  var sub = ALGO_SUB.filter(function (s) { return s.id === S.pg; })[0];
  if (sub) {
    GW.algoDetail(parseInt(sub.id.replace('algo', ''), 10));
  } else if (R[S.pg]) {
    R[S.pg]();
  } else {
    R.dash();
  }
}
GW.render = render;

/* ================= 登录页 ================= */
R.login = function () {
  headerActions('');
  var c = $('content');
  c.innerHTML = '<div class="login-wrap"><div class="login-card"><h2>网关管理</h2>' +
    '<div class="form-group"><label for="loginPwd">管理密码</label><input type="password" id="loginPwd" placeholder="请输入密码"></div>' +
    '<button class="btn btn-primary" id="loginBtn">登录</button></div></div>';
  var doLogin = async function () {
    var pwdEl = $('loginPwd'), btn = $('loginBtn');
    if (btn.disabled) return; // 防重入
    var pwd = pwdEl.value;
    if (!pwd) { toast('请输入密码', 'error'); return; }
    btn.disabled = true; btn.textContent = '登录中...';
    try {
      var d = await api('/login', { method: 'POST', body: JSON.stringify({ password: pwd }) });
      S.token = d.token || d.access_token;
      localStorage.setItem('t', S.token);
      toast('登录成功', 'success');
      S.pg = 'dash';
      render();
    } catch (e) {
      toast(e.message || '登录失败', 'error');
      btn.disabled = false; btn.textContent = '登录';
    }
  };
  $('loginBtn').addEventListener('click', doLogin);
  $('loginPwd').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
};

/* ================= 全局动作 ================= */
actions['dismiss-modal'] = function () { closeModal(); };
actions['reload-page'] = function () { render(); };
actions['logout'] = function () {
  S.token = '';
  localStorage.removeItem('t');
  S.pg = 'dash';
  toast('已退出登录', 'info');
  render();
};
actions['nav'] = function (ds) { navTo(ds.page); closeSidebar(); };

/* ================= 全局事件委托 =================
 * 所有 [data-act] 按钮统一分发；动态数据走 dataset 索引 + cache，
 * 禁止把服务端数据拼进内联事件属性 */
document.addEventListener('click', function (e) {
  var el = e.target.closest('[data-act]');
  if (!el) return;
  var act = el.dataset.act;
  var fn = actions[act];
  if (fn) fn(el.dataset, el);
});

$('modalOverlay').addEventListener('click', function (e) {
  if (e.target === this) closeModal();
});
$('hamburgerBtn').addEventListener('click', function () {
  $('sidebar').classList.toggle('open');
  $('overlay').classList.toggle('show');
});
$('overlay').addEventListener('click', closeSidebar);

/* ================= Init ================= */
document.addEventListener('DOMContentLoaded', function () {
  render();
});
})();
