/* AQUA 平台管理后台 — admin.js
 *
 * 安全模式（必须保留，勿回退）：
 *  - 用户表格的操作按钮只携带 data-act / data-id / data-username-idx（行索引），
 *    用户名等动态数据绝不内插进 HTML 属性（原 escAttr 只转义引号，挡不住反斜杠等逃逸，构成存储型 XSS），
 *    实际用户名在一次性事件委托里从缓存的 state.users 按索引读取；
 *  - 所有动态文本经 esc() 或 textContent 输出；
 *  - 鉴权走 httponly cookie（credentials:'same-origin'），与后端 /api/admin/* 契约不变。
 */
(function () {
'use strict';

// ========== State ==========
const state = {
  users: [],      // 当前页用户缓存（事件委托按索引取用户名）
  total: 0,
  page: 1,
  pageSize: 20,
  modalAction: null,
};

const $ = (id) => document.getElementById(id);

// ========== HTTP（契约与重构前一致：cookie 会话 + /api/admin/*） ==========
const API = {
  async req(method, url, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin' };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      let msg = `请求失败 (${resp.status})`;
      try { const e = await resp.json(); msg = e.detail || msg; } catch {}
      throw new Error(msg);
    }
    return resp.json();
  },
  get: (url) => API.req('GET', url),
  post: (url, body) => API.req('POST', url, body),
  put: (url, body) => API.req('PUT', url, body),
  del: (url) => API.req('DELETE', url),
};

// ========== Toast ==========
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg; // 纯文本输出
  $('toastWrap').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 2600);
}

// ========== 二次确认弹窗（封禁/解封/删除） ==========
function showModal(title, body, danger, onConfirm) {
  $('modalTitle').textContent = title;
  $('modalBody').textContent = body;
  const btn = $('modalConfirmBtn');
  btn.textContent = danger ? '确定删除' : '确定';
  btn.className = 'btn btn-sm ' + (danger ? 'btn-danger' : 'btn-warning');
  state.modalAction = onConfirm;
  $('modalOverlay').classList.add('show');
}
function closeModal() {
  $('modalOverlay').classList.remove('show');
  state.modalAction = null;
}
async function modalConfirm() {
  const fn = state.modalAction;
  closeModal();
  if (fn) await fn();
}

// ========== 鉴权 ==========
function showLogin() { $('adminShell').classList.add('hidden'); $('loginScreen').classList.remove('hidden'); }
function showShell() { $('loginScreen').classList.add('hidden'); $('adminShell').classList.remove('hidden'); }

async function doLogin(password) {
  const btn = $('loginBtn');
  const errEl = $('loginError');
  btn.disabled = true; btn.textContent = '登录中...'; errEl.textContent = '';
  try {
    await API.post('/api/admin/login', { password });
    showShell();
    loadUsers();
  } catch (e) {
    errEl.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = '登录';
  }
}

async function checkAuth() {
  try {
    const r = await API.get('/api/admin/check');
    if (r.logged_in) { showShell(); loadUsers(); }
  } catch {}
}

async function logout() {
  try { await API.post('/api/admin/logout'); } catch {}
  showLogin();
  toast('已登出', 'info');
}

// ========== 用户列表（分页 + 搜索状态保持：搜索/筛选输入在骨架中，不随表格重渲染丢失） ==========
function loadingHtml() { return '<div class="spinner"></div>'; }
function emptyHtml(msg) { return `<div class="state-block"><span class="ico">&#128230;</span><div class="msg">${esc(msg)}</div></div>`; }
function errorHtml(msg) {
  return `<div class="state-block"><span class="ico">&#9888;</span><div class="msg">加载失败: ${esc(msg)}</div><button class="btn btn-sm btn-outline" data-act="retry">重试</button></div>`;
}

async function loadUsers() {
  const wrap = $('tableWrap');
  wrap.innerHTML = loadingHtml();
  try {
    const search = $('searchInput').value.trim();
    const status = $('statusFilter').value;
    const r = await API.get(`/api/admin/users?page=${state.page}&page_size=${state.pageSize}&search=${encodeURIComponent(search)}&status=${status}`);
    state.users = r.users || [];
    state.total = r.total || 0;
    const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    if (state.page > totalPages) { // 删除等操作后页码越界：回到第 1 页重新拉取
      state.page = 1;
      loadUsers();
      return;
    }
    renderTable();
  } catch (e) {
    wrap.innerHTML = errorHtml(e.message);
    updateStats();
  }
}

function renderTable() {
  const wrap = $('tableWrap');
  const users = state.users;
  if (!users.length) {
    wrap.innerHTML = emptyHtml('暂无用户数据');
    updateStats();
    return;
  }

  let html = `<table><thead><tr>
    <th>ID</th><th>用户名</th><th>昵称</th><th>邮箱</th><th>状态</th><th>注册时间</th><th>操作</th>
  </tr></thead><tbody>`;
  // 安全模式：按钮只带 data-id 与 data-username-idx（行索引），用户名不进属性
  users.forEach((u, i) => {
    const active = u.status === 'active';
    html += `<tr>
      <td class="mono text-muted">${Number(u.id)}</td>
      <td><strong>${esc(u.username)}</strong></td>
      <td class="text-muted">${esc(u.display_name || '-')}</td>
      <td class="text-muted">${esc(u.email)}</td>
      <td><span class="status-badge ${active ? 'status-active' : 'status-banned'}">${active ? '正常' : '封禁'}</span></td>
      <td class="text-muted mono">${fmtTime(u.created_at)}</td>
      <td><div class="cell-actions">
        ${active
          ? `<button class="btn btn-sm btn-warning" data-act="ban" data-id="${Number(u.id)}" data-username-idx="${i}">封禁</button>`
          : `<button class="btn btn-sm btn-success" data-act="unban" data-id="${Number(u.id)}" data-username-idx="${i}">解封</button>`
        }
        <button class="btn btn-sm btn-danger" data-act="delete" data-id="${Number(u.id)}" data-username-idx="${i}">删除</button>
      </div></td>
    </tr>`;
  });
  html += '</tbody></table>';

  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  html += `<div class="pagination">
    <span class="page-info">共 ${state.total} 条，第 ${state.page}/${totalPages} 页</span>
    <div class="page-btns">
      <button class="btn btn-sm" data-page="${state.page - 1}" ${state.page <= 1 ? 'disabled' : ''}>上一页</button>
      <button class="btn btn-sm" data-page="${state.page + 1}" ${state.page >= totalPages ? 'disabled' : ''}>下一页</button>
    </div>
  </div>`;

  wrap.innerHTML = html;
  updateStats();
}

function updateStats() {
  $('statsBar').innerHTML = `
    <div class="stat-box"><div class="num">${state.total}</div><div class="lbl">总用户</div></div>
    <div class="stat-box"><div class="num">${state.users.filter(u => u.status === 'active').length}</div><div class="lbl">当前页正常</div></div>
    <div class="stat-box"><div class="num">${state.users.filter(u => u.status === 'banned').length}</div><div class="lbl">当前页封禁</div></div>
  `;
}

function goPage(p) {
  state.page = p;
  loadUsers();
}

let searchTimer = null;
function onSearchInput() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.page = 1; loadUsers(); }, 400);
}

// ========== 操作（均带二次确认） ==========
function banUser(id, name) {
  showModal('封禁用户', `确定要封禁用户「${name}」吗？`, false, async () => {
    try {
      await API.put(`/api/admin/users/${id}/ban`);
      toast(`用户 ${name} 已封禁`, 'success');
      loadUsers();
    } catch (e) { toast(e.message, 'error'); }
  });
}

function unbanUser(id, name) {
  showModal('解封用户', `确定要解封用户「${name}」吗？`, false, async () => {
    try {
      await API.put(`/api/admin/users/${id}/unban`);
      toast(`用户 ${name} 已解封`, 'success');
      loadUsers();
    } catch (e) { toast(e.message, 'error'); }
  });
}

function deleteUser(id, name) {
  showModal('删除用户', `确定要删除用户「${name}」及其所有数据吗？此操作不可恢复！`, true, async () => {
    try {
      await API.del(`/api/admin/users/${id}`);
      toast(`用户 ${name} 已删除`, 'success');
      loadUsers();
    } catch (e) { toast(e.message, 'error'); }
  });
}

// ========== 工具 ==========
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtTime(t) { if (!t) return '-'; return t.slice(0, 10) + ' ' + t.slice(11, 16); }

// ========== Init（一次性绑定，替代原先的内联 onclick/oninput/onchange） ==========
$('loginForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const pwd = $('passwordInput').value;
  if (pwd) doLogin(pwd);
});
$('refreshBtn').addEventListener('click', loadUsers);
$('logoutBtn').addEventListener('click', logout);
$('searchInput').addEventListener('input', onSearchInput);
$('statusFilter').addEventListener('change', () => { state.page = 1; loadUsers(); });

$('modalCancel').addEventListener('click', closeModal);
$('modalConfirmBtn').addEventListener('click', modalConfirm);
$('modalOverlay').addEventListener('click', (e) => {
  if (e.target === $('modalOverlay')) closeModal();
});

// 事件委托：封禁/解封/删除/翻页/重试统一分发；
// 用户名从 state.users 按 data-username-idx 读取，杜绝动态数据进内联事件属性
$('tableWrap').addEventListener('click', (e) => {
  const pageBtn = e.target.closest('button[data-page]');
  if (pageBtn && !pageBtn.disabled) { goPage(Number(pageBtn.dataset.page)); return; }

  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  if (btn.dataset.act === 'retry') { loadUsers(); return; }

  const u = state.users[Number(btn.dataset.usernameIdx)];
  if (!u) return;
  const id = Number(btn.dataset.id);
  if (btn.dataset.act === 'ban') banUser(id, u.username);
  else if (btn.dataset.act === 'unban') unbanUser(id, u.username);
  else if (btn.dataset.act === 'delete') deleteUser(id, u.username);
});

document.addEventListener('DOMContentLoaded', checkAuth);
})();
