// 职责：密钥管理——列表/排序、创建(上限5)、弹窗查看完整密钥、复制、启停、删除、API地址展示

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { ensureAuth } from '../auth.js';
import { Toast, Modal, Loader, emptyState, errorState, escapeHtml, escapeAttr, registerSecret, applySort, copyToClipboard } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

const BASE_URL = location.origin + '/v1';
const FULL_URL = BASE_URL + '/chat/completions';
const MAX_KEYS = 5;

const state = { data: [], sort: { field: 'created_at', order: 'desc' } };

export async function renderKeys() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/keys');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">密钥管理</h1>
        <button class="btn primary" id="create-key-btn">${Icons.plus(16)} 创建密钥</button>
      </div>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">API密钥列表</h3>
          <span class="muted" id="keys-count">-</span>
        </div>
        <div id="keys-list"></div>
      </div>
      <div class="card">
        <div class="card-header"><h3 class="card-title">API地址</h3></div>
        <div class="api-url-section">
          <div class="form-group">
            <label class="form-label">Base URL（OpenAI SDK 使用）</label>
            <div class="input-group">
              <input type="text" class="input" readonly value="${escapeAttr(BASE_URL)}">
              <button class="btn" data-copy="${escapeAttr(BASE_URL)}">${Icons.copy(14)} 复制</button>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">完整端点 URL</label>
            <div class="input-group">
              <input type="text" class="input" readonly value="${escapeAttr(FULL_URL)}">
              <button class="btn" data-copy="${escapeAttr(FULL_URL)}">${Icons.copy(14)} 复制</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
  document.getElementById('create-key-btn').onclick = showCreateKeyModal;
  await loadKeys();
}

async function loadKeys() {
  const container = document.getElementById('keys-list');
  if (!container) return;
  Loader.skeleton(container, 4);
  try {
    const keys = await API.get('/api/user/keys');
    state.data = keys || [];
    const countEl = document.getElementById('keys-count');
    if (countEl) countEl.textContent = `${state.data.length} / ${MAX_KEYS}`;
    renderKeysList();
  } catch (err) {
    container.innerHTML = '';
    container.appendChild(errorState('加载密钥失败：' + err.message, loadKeys));
  }
}

function renderKeysList() {
  const container = document.getElementById('keys-list');
  if (!container) return;
  const keys = applySort(state.data, state.sort.field, state.sort.order);
  if (!keys.length) {
    container.innerHTML = emptyState('暂无API密钥，点击右上角创建', 'key');
    return;
  }
  container.innerHTML = `
    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th data-sort="key_prefix">密钥</th>
            <th data-sort="label">备注</th>
            <th data-sort="status">状态</th>
            <th data-sort="created_at">创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${keys.map((k) => {
            const isActive = k.status === 'active';
            const prefix = (k.key_prefix || '-') + (k.key_prefix && k.key_prefix.length <= 8 ? '...' : '');
            return `
            <tr>
              <td>
                <div class="key-cell">
                  <code class="key-prefix">${escapeHtml(prefix)}</code>
                  <button class="btn xs" data-act="reveal" data-id="${escapeAttr(k.id)}" title="查看完整密钥">${Icons.eye(12)}</button>
                  <button class="btn xs" data-act="copy-key" data-id="${escapeAttr(k.id)}" title="复制完整密钥">${Icons.copy(12)}</button>
                </div>
              </td>
              <td>${escapeHtml(k.label || '未命名')}</td>
              <td><span class="tag ${isActive ? 'active' : ''}"><span class="status-dot ${isActive ? 'active' : ''}"></span>${isActive ? 'active' : escapeHtml(k.status)}</span></td>
              <td class="muted">${escapeHtml(fmtTime(k.created_at))}</td>
              <td>
                <div class="btn-group">
                  <button class="btn xs ${isActive ? '' : 'primary'}" data-act="toggle" data-id="${escapeAttr(k.id)}" title="${isActive ? '停用密钥' : '启用密钥'}">
                    ${isActive ? Icons.stop(12) : Icons.check(12)} ${isActive ? '停用' : '启用'}
                  </button>
                  <button class="btn xs" data-copy="${escapeAttr(BASE_URL)}" title="复制Base URL">${Icons.copy(12)} Base</button>
                  <button class="btn xs" data-copy="${escapeAttr(FULL_URL)}" title="复制完整URL">${Icons.copy(12)} Full</button>
                  <button class="btn xs danger" data-act="delete" data-id="${escapeAttr(k.id)}">${Icons.trash(12)} 删除</button>
                </div>
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;

  // 排序
  container.querySelectorAll('th[data-sort]').forEach((th) => {
    th.classList.toggle('sort-asc', state.sort.field === th.dataset.sort && state.sort.order === 'asc');
    th.classList.toggle('sort-desc', state.sort.field === th.dataset.sort && state.sort.order === 'desc');
    th.onclick = () => {
      const field = th.dataset.sort;
      if (state.sort.field === field) {
        state.sort.order = state.sort.order === 'asc' ? 'desc' : 'asc';
      } else {
        state.sort = { field, order: 'asc' };
      }
      renderKeysList();
    };
  });

  // 行内操作（事件委托，禁止内联事件拼接数据）
  container.querySelectorAll('[data-act]').forEach((btn) => {
    const id = btn.dataset.id;
    btn.onclick = () => {
      const act = btn.dataset.act;
      if (act === 'reveal') showKeyModal(id);
      else if (act === 'copy-key') copyFullKey(id, btn);
      else if (act === 'toggle') toggleKeyStatus(id);
      else if (act === 'delete') deleteKey(id);
    };
  });
}

function fmtTime(dateStr) {
  if (!dateStr) return '-';
  try {
    return new Date(dateStr).toLocaleString('zh-CN', { hour12: false });
  } catch (_) { return '-'; }
}

/** 获取完整密钥（优先缓存） */
async function fetchFullKey(keyId) {
  const result = await API.get('/api/user/keys/' + keyId + '/reveal');
  return result.key;
}

/** 弹窗查看完整密钥（不再跳页/不再直接铺在表格里） */
async function showKeyModal(keyId) {
  Modal.show({
    title: '查看完整密钥',
    body: '<div class="loader"><div class="loader-spinner">' + Icons.spinner(24) + '</div><div class="loader-text">解密中...</div></div>',
  });
  let fullKey;
  try {
    fullKey = await fetchFullKey(keyId);
  } catch (err) {
    Toast.error('获取密钥失败：' + err.message);
    Modal.close();
    return;
  }
  const ref = registerSecret(fullKey);
  Modal.show({
    title: '完整API密钥',
    body: `
      <div class="key-created-info">
        <div class="alert alert-warning">${Icons.warn(16)}<span>密钥仅自己可见，请勿泄露给他人</span></div>
        <div class="form-group">
          <label class="form-label">完整密钥</label>
          <div class="input-group">
            <input type="text" class="input key-full-input" readonly value="${escapeAttr(fullKey)}">
            <button class="btn" data-copy-ref="${escapeAttr(ref)}" data-copy-msg="密钥已复制到剪贴板">${Icons.copy(14)} 复制</button>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Base URL</label>
          <div class="input-group">
            <input type="text" class="input" readonly value="${escapeAttr(BASE_URL)}">
            <button class="btn" data-copy="${escapeAttr(BASE_URL)}">${Icons.copy(14)} 复制</button>
          </div>
        </div>
      </div>
    `,
    footer: '<button class="btn primary" id="key-modal-close">' + Icons.check(14) + ' 关闭</button>',
    onShow: (body, footer) => {
      footer.querySelector('#key-modal-close').onclick = () => Modal.close();
    },
  });
}

async function copyFullKey(keyId, btn) {
  let key;
  try {
    key = await fetchFullKey(keyId);
  } catch (err) {
    Toast.error('获取密钥失败：' + err.message);
    return;
  }
  await copyToClipboard(key, '密钥已复制到剪贴板', btn);
}

async function toggleKeyStatus(keyId) {
  try {
    const result = await API.put('/api/user/keys/' + keyId + '/toggle', {});
    Toast.success(result.message || '状态已切换');
    await loadKeys();
  } catch (err) {
    Toast.error(err.message);
  }
}

async function deleteKey(keyId) {
  const ok = await Modal.confirm({
    title: '删除密钥',
    body: '<p>确定要删除此API密钥吗？删除后使用该密钥的请求将失败。</p>',
    danger: true,
    okText: '确认删除',
  });
  if (!ok) return;
  try {
    await API.delete('/api/user/keys/' + keyId);
    Toast.success('密钥已删除');
    await loadKeys();
  } catch (err) {
    Toast.error(err.message);
  }
}

async function showCreateKeyModal() {
  const activeKeys = state.data.filter((k) => k.status === 'active');
  if (activeKeys.length >= MAX_KEYS) {
    Toast.warn(`已达密钥上限(${MAX_KEYS}个)，请先删除不需要的密钥`);
    return;
  }
  Modal.show({
    title: '创建API密钥',
    body: `
      <div class="form-group">
        <label class="form-label">备注（可选）</label>
        <input type="text" id="key-label" class="input" placeholder="例如：开发环境" maxlength="64">
      </div>
    `,
    footer: `
      <button class="btn" data-act="cancel">取消</button>
      <button class="btn primary" id="create-key-confirm">${Icons.plus(14)} 创建</button>
    `,
    onShow: (body, footer) => {
      footer.querySelector('[data-act="cancel"]').onclick = () => Modal.close();
      footer.querySelector('#create-key-confirm').onclick = async () => {
        const label = body.querySelector('#key-label').value.trim();
        const btn = footer.querySelector('#create-key-confirm');
        btn.disabled = true;
        btn.innerHTML = Icons.spinner(14) + ' 创建中...';
        try {
          const result = await API.post('/api/user/keys', { label });
          Modal.close();
          showKeyCreatedModal(result);
          await loadKeys();
          Toast.success('密钥创建成功');
        } catch (err) {
          Toast.error(err.message);
          btn.disabled = false;
          btn.innerHTML = Icons.plus(14) + ' 创建';
        }
      };
    },
  });
}

/** 创建成功后的密钥展示弹窗（完整密钥仅此一次完整展示） */
function showKeyCreatedModal(result) {
  const ref = registerSecret(result.key);
  Modal.show({
    title: '密钥创建成功',
    body: `
      <div class="key-created-info">
        <div class="alert alert-warning">
          ${Icons.warn(16)}
          <span>请立即保存密钥，关闭后将无法再次查看完整内容</span>
        </div>
        <div class="form-group">
          <label class="form-label">完整API密钥</label>
          <div class="input-group">
            <input type="text" class="input key-full-input" id="new-key-value" readonly value="${escapeAttr(result.key)}">
            <button class="btn" data-copy-ref="${escapeAttr(ref)}" data-copy-msg="密钥已复制到剪贴板">${Icons.copy(14)} 复制</button>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">密钥前缀</label>
          <input type="text" class="input" readonly value="${escapeAttr(result.key_prefix)}">
        </div>
      </div>
      <div class="card-subtitle" style="margin-top:12px">API地址（点击复制）</div>
      <div class="api-urls" style="margin-top:8px">
        <div class="api-url-row">
          <span class="api-url-label">Base URL:</span>
          <code>${escapeHtml(BASE_URL)}</code>
          <button class="btn xs" data-copy="${escapeAttr(BASE_URL)}">${Icons.copy(12)}</button>
        </div>
        <div class="api-url-row">
          <span class="api-url-label">完整端点:</span>
          <code>${escapeHtml(FULL_URL)}</code>
          <button class="btn xs" data-copy="${escapeAttr(FULL_URL)}">${Icons.copy(12)}</button>
        </div>
      </div>
    `,
    footer: `<button class="btn primary" id="key-created-ok">${Icons.check(14)} 我已保存</button>`,
    onShow: (body, footer) => {
      footer.querySelector('#key-created-ok').onclick = () => Modal.close();
    },
  });
}
